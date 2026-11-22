# NZXT Lift Elite su Linux

Strumenti **non ufficiali** per il mouse **NZXT Lift Elite**, via cavo
(USB `1e71:2129`) o tramite il ricevitore 2.4 GHz (USB `1e71:2101`):

- **Lift Elite Control**: app grafica GTK4/libadwaita, in italiano, per DPI,
  polling, lift-off, motion sync, tasti e profili con nome
- **`liftctl`**: riga di comando con le stesse funzioni
- **plugin CoolerControl** (opzionale): stadio DPI, polling, lift-off, motion
  sync e LED di un profilo, direttamente dall'interfaccia di CoolerControl
- **`nzxt_lift_elite`**: la libreria Python che usano tutti e tre

Il protocollo HID non è documentato da NZXT: è stato ricostruito osservando
i pacchetti che NZXT CAM invia al mouse (dettagli in [`PROTOCOL.md`](PROTOCOL.md)).
Non è affiliato a NZXT. Ogni scrittura è letta di nuovo per verificarla e,
prima di scrivere, il programma salva un backup dei profili. Nessun comando
di aggiornamento firmware è raggiungibile: la libreria li rifiuta.

![Lift Elite Control](docs/screenshot.png)

## Installazione su CachyOS (passo per passo)

I comandi sono per **fish**, la shell di CachyOS. Solo quelli con `sudo`
richiedono la password di amministratore; `makepkg -si` la chiede da solo
quando installa il pacchetto.

**1. Copia la cartella del progetto sul PC** (ad esempio in `~/src/nzxt-lift-elite`)
ed entra nella cartella del pacchetto:

```fish
cd ~/src/nzxt-lift-elite/packaging/arch
```

**2. Compila e installa** (crea due pacchetti locali, non viene scaricato nulla dall'AUR):

```fish
makepkg -si
```

Come utente normale, **senza** `sudo` davanti: alla fine chiede la password per
`pacman`. Installa `nzxt-lift-elite` (libreria, `liftctl`, l'app, icona, regola udev)
e `nzxt-lift-elite-coolercontrol` (il plugin). Le dipendenze (`python-gobject`,
`gtk4`, `libadwaita`, `python-grpcio`…) le installa pacman. In alternativa
`paru -Ui` nella stessa cartella fa la stessa cosa.

Se il plugin non ti serve: `makepkg -s` e poi `sudo pacman -U nzxt-lift-elite-0.2.0-1-any.pkg.tar.zst`.

**3. Permessi sul mouse** — la regola udev è già installata in
`/usr/lib/udev/rules.d/70-nzxt-lift-elite.rules` e dà accesso all'utente collegato
(tag `uaccess`, nessun gruppo da aggiungere). Stacca e riattacca il cavo e/o il
ricevitore, oppure:

```fish
sudo udevadm control --reload-rules
sudo udevadm trigger -s hidraw
```

**4. Primo test, di sola lettura** (non scrive nulla sul mouse, senza `sudo`):

```fish
liftctl show
```

Deve elencare i 5 profili con polling, lift-off, stadi DPI e tasti. Se dice
"permesso negato", ripeti il punto 3. Altre verifiche di sola lettura:
`liftctl devices` (cosa è collegato) e `liftctl firmware-info`.

**5. Apri l'app**: cerca *Lift Elite Control* nel menu di KDE, oppure da terminale
`lift-elite-control`. All'avvio legge soltanto; scrive solo quando premi
**Applica** e confermi.

**6. Plugin CoolerControl** (solo se usi CoolerControl e hai installato il secondo pacchetto):

```fish
sudo systemctl restart coolercontrold
```

### Disinstallare

```fish
sudo pacman -R nzxt-lift-elite-coolercontrol nzxt-lift-elite
```

I profili salvati restano in `~/.config/nzxt-lift-elite/` (cancellali a mano se vuoi).

### Senza pacchetto, dal checkout

Per provare senza installare (servono le dipendenze della GUI):

```fish
sudo pacman -S --needed python-gobject gtk4 libadwaita
sudo install -m 644 70-nzxt-lift-elite.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules; sudo udevadm trigger -s hidraw
./liftctl show
./lift-elite-control
```

Solo le prime tre righe usano `sudo`. `./lift-elite-control --mock` apre l'app su un mouse
emulato, senza toccare il dispositivo vero.

## Lift Elite Control (GUI)

- **stato del dispositivo** in sola lettura: collegamento (cavo/wireless), nodo hidraw,
  versioni firmware di mouse, bridge, updater e ricevitore
- **selettore del profilo** 1..5, con il colore del LED di ciascuno: sceglie il
  profilo da *modificare*, non lo attiva sul mouse (per attivarlo: pressione
  lunga sul tasto viola)
- **prestazioni**: polling (avvisa che 8000 Hz va solo via cavo), lift-off 1/2 mm,
  motion sync, colore del LED del profilo (si vede per circa due secondi quando
  cambi profilo tenendo premuto il tasto viola; in uso normale, e in carica, il
  LED è una spia di stato e può restare verde)
- **anteprima** con la sagoma del Lift Elite: clicca i tasti, inclusa la vista del
  fondo per il tasto viola
- **5 stadi DPI** con slider e campo numerico (50..26000, passi di 50); lo stadio
  attivo salvato nel profilo è evidenziato
- **collegamento in tempo reale**: se stacchi o attacchi il cavo o il ricevitore
  l'app se ne accorge da sola (preferisce il cavo se ci sono entrambi), mostra lo
  stato nell'intestazione, tiene le modifiche non salvate e non scrive nulla da sola
- **tasti**: clicca un tasto sul disegno del mouse (o nella lista) e scegli l'azione
  da un elenco con ricerca: clic, tasti multimediali, lettere, F1–F24, modificatori…
- **profili con nome**: salva, carica, elimina, importa/esporta JSON
- **Applica** scrive subito solo ciò che è cambiato e verifica rileggendo (un
  backup silenzioso viene salvato in `~/.config/nzxt-lift-elite/backups`);
  **Annulla modifiche** torna ai valori letti
- tema chiaro/scuro (segue il sistema), notifiche, italiano (`LIFT_ELITE_LANG=en`
  per l'inglese)

Opzioni: `--device /dev/hidrawN`, `--transport wired|wireless`, `--theme dark|light`, `--mock`.
L'hotplug usa `pyudev` (dipendenza del pacchetto); se manca, controlla `/sys/class/hidraw` ogni 1,5 s.

## liftctl (riga di comando)

I profili si indicano **1..5** (come nella GUI). Nella memoria del mouse e nei file
JSON il campo `profile` resta lo slot interno **0..4**. Gli stadi DPI sono **1..5**
(il byte sul filo è 0..4: 0 è il primo valore).

```fish
liftctl show                          # tutti i profili (sola lettura)
liftctl show --profile 1 --json       # un profilo in JSON (slot interno 0)
liftctl devices                       # cavo / ricevitore trovati
liftctl firmware-info                 # versioni firmware (sola lettura)
liftctl debug-dump                    # sola lettura: hex delle risposte note
                                      # (esegui, cambia profilo col tasto viola, riesegui)

liftctl set --profile 1 --polling 1000 --liftoff 2
liftctl set --profile 1 --dpi 400,800,1600,3200,6400 --stage 2
liftctl set --profile 1 --dpi-stage 2=1000 --motion-sync off
liftctl set --profile 2 --color '#ff0000'
liftctl restore-led-colors             # colori LED di fabbrica (solo quel campo)

liftctl remap --profile 1 --button forward --action volume-up
liftctl remap --profile 1 --button dpi --action key:0x04    # tasto "a"
liftctl remap --profile 1 --reset                            # tasti di fabbrica
liftctl actions volume                                       # cerca un'azione

liftctl save FPS --profile 1          # profilo con nome
liftctl profiles                      # elenca i profili con nome
liftctl load FPS --profile 3          # caricalo in un altro slot
liftctl delete FPS
liftctl save --all tutti.json         # tutti e 5 in un file
liftctl --dry-run load --all tutti.json
```

- **Tasti**: `left`, `middle`, `right`, `forward`, `back`, `dpi`
- **Azioni**: `left-click`, `middle-click`, `right-click`, `mouse-forward`,
  `mouse-back`, `dpi-cycle`, `mute`, `volume-up`, `volume-down`, `play-pause`,
  `next-track`, `previous-track`, `stop`, lettere e cifre (`a`, `5`), `f1`..`f24`,
  `enter`, `escape`, `ctrl`, `shift`, `alt`… (`liftctl actions` le elenca tutte)
- `key:0xNN` assegna un codice HID della tastiera (pagina 0x07). I codici fuori
  dalla tabella nota richiedono `--allow-unknown-code`.

`set`, `remap` e `load` fanno sempre **lettura → modifica dei soli campi richiesti →
scrittura → rilettura di verifica**. I byte non ancora decodificati restano com'erano.
`liftctl set --color '#rrggbb'` scrive il colore del LED del profilo (RGB565
little-endian, come i valori di fabbrica) e lo rilegge. I colori di fabbrica si
vedono per circa due secondi con una pressione lunga sul tasto viola (confermato);
una scrittura personalizzata è in prova sul mouse reale.

`liftctl restore-led-colors` (opzionale `--profile N`) scrive solo i byte di
fabbrica e li rilegge:

| profilo | colore | circa | byte RGB565 LE |
|---|---|---|---|
| 1 | Blue | `#0000ff` | `1f00` |
| 2 | Red | `#ff0000` | `00f8` |
| 3 | Green | `#00ff00` | `e007` |
| 4 | Yellow | `#ffaa00` | `40fd` |
| 5 | Purple | `#ff00ff` | `1ff8` |

Se in precedenza i profili 2 e 5 (slot interni 1 e 4) sono stati cambiati
(letti `#ffbe6b` e `#ffffff`), questo comando li riporta ai valori di fabbrica.

Opzioni globali: `--dry-run` (legge ma non scrive: stampa i pacchetti),
`--debug` (dump esadecimale di ogni pacchetto), `--device /dev/hidrawN`,
`--transport auto|wired|wireless` (con cavo e ricevitore entrambi collegati, `auto`
sceglie il cavo).

### File di configurazione

In `~/.config/nzxt-lift-elite/`:

- `profiles/*.json` — profili con nome (GUI e `liftctl save NOME`)
- `slots/slot-N.json` — l'ultimo stato scritto in ciascuno slot
- `backups/` — copia dei 5 profili prima di ogni scrittura (gli ultimi 50)

Esempio di file (formato 2):

```json
{
  "device": "NZXT Lift Elite",
  "format": 2,
  "name": "FPS",
  "profiles": [
    {
      "polling_hz": 1000,
      "liftoff": 1,
      "dpi_stages": [400, 800, 1600, 3200, 6400],
      "dpi_stage": 2,
      "motion_sync": true,
      "color": "#0000ff",
      "buttons": {"left": "left-click", "middle": "middle-click", "right": "right-click",
                  "forward": "volume-up", "back": "mouse-back", "dpi": "dpi-cycle"}
    }
  ]
}
```

I campi omessi non vengono toccati quando carichi il file. I profili con nome non
contengono il colore del LED: caricandoli in un altro slot il colore di quello
slot resta invariato (i file `--all`, gli export e i backup lo contengono e lo
applicano). Anche i file del formato 1 (versione precedente) si caricano.

## Plugin CoolerControl

Il mouse compare in CoolerControl come dispositivo **Lift Elite** con cinque canali
"illuminazione" usati come selettori:

| canale | valori |
|---|---|
| DPI stage | stadio 1..5 (con i DPI attuali nell'etichetta) |
| Polling rate | 250 … 8000 Hz (8000 solo via cavo) |
| Lift-off | 1 mm / 2 mm |
| Motion sync | on / off |
| Profile LED | un colore |

Agiscono su **un** profilo, scelto nella pagina del plugin (Impostazioni → Plugin →
NZXT Lift Elite; predefinito il profilo 1). Quale profilo è attivo sul mouse non è ancora confermato nella GUI (c'è un candidato `0x44`, vedi sotto), quindi nel plugin va scelto a mano. Il plugin scrive solo se il valore cambia, rilegge per
verificare, e *Reset* ripristina il valore letto all'avvio del plugin. Batteria e altri
sensori non sono esposti: la risposta che probabilmente contiene la batteria non è
ancora decodificata con certezza.

File: `/var/lib/coolercontrol/plugins/nzxt_lift_elite/` (manifest, pagina, `config.json`)
e `/usr/lib/nzxt-lift-elite/coolercontrol/` (server Python e stub gRPC). Log:
`journalctl -u coolercontrold -u cc-plugin-nzxt_lift_elite -f`.

GUI, `liftctl` e plugin possono girare insieme: un lock sul nodo hidraw impedisce
che i pacchetti si mescolino.

## Cosa non è ancora noto

- **profilo attivo**: c'è un candidato molto credibile (comando di lettura
  `0x44`, scrittura `0x45`, stessi nomi CAM `CMD_GET/SET_PROFILE_NUMBER`;
  confermato nel firmware e in una cattura USB di CAM). La scrittura non è ancora
  abilitata. Per confermarlo sul mouse: `liftctl debug-dump`, tieni premuto
  a lungo il tasto viola, `liftctl debug-dump` di nuovo e confronta la riga
  `44 get-active-profile`. `liftctl show` stampa già l'interpretazione
  sperimentale di `0x44`.
- la lettura dei tasti e le versioni del **ricevitore** sono state catturate via cavo;
  attraverso il ricevitore si assume lo stesso protocollo
- il campo **colore del LED** (byte 21..22, RGB565 little-endian) coincide coi colori
  di fabbrica del manuale, che si vedono per circa due secondi con una pressione
  lunga sul tasto viola (confermato). CAM non ha un editor; una scrittura
  personalizzata (GUI, `liftctl set --color`, plugin) è in prova sul mouse reale.
  Per riportare i byte di fabbrica: `liftctl restore-led-colors`
- **batteria**: non esposta (vedi sopra)
- se il mouse wireless dorme, le letture vanno in timeout: muovilo e riprova

## Libreria Python

```python
from nzxt_lift_elite import LiftElite

with LiftElite.open() as mouse:          # transport="wired"/"wireless" per forzare
    p = mouse.read_profile(0)      # la libreria usa gli slot 0..4
    nuovo = p.with_changes(polling_hz=1000, dpi_stages={2: 1000}, buttons={"forward": "volume-up"})
    mouse.apply_profile(nuovo)           # scrive solo le differenze e verifica
```

Moduli: `protocol` (pacchetti, nessuna I/O), `actions` (tabella delle azioni),
`profile` (modello e JSON), `device` (rilevamento e I/O), `store` (profili con nome),
`mock` (mouse emulato per i test), `cli`, `gui`.

## Test

Senza mouse collegato, dalla radice del progetto:

```fish
python3 -m unittest discover -s tests -t .
```

Usano un hidraw finto (socket) che emula il mouse. Il test della GUI parte solo con
un display (`xvfb-run -a python3 -m unittest tests.test_gui_smoke`); quello gRPC del
plugin solo con `python-grpcio` e `python-grpcio-tools`.

## Licenza e avvertenze

GPL-3.0-or-later (vedi `LICENSE`); i file `.proto` del plugin vengono da CoolerControl
(GPL-3.0). Lavoro indipendente basato su un protocollo ricostruito e non supportato:
usalo a tuo rischio. NZXT, CAM e Lift Elite sono marchi dei rispettivi proprietari.
Il pacchetto non contiene firmware né strumenti per aggiornarlo.
