# Reverse engineering dell'NZXT Lift Elite

*[Read in English](REVERSE_ENGINEERING.md)*

Questa guida spiega come è stato ricostruito il protocollo proprietario del
mouse NZXT Lift Elite, così puoi verificare i risultati, estenderli o usare lo
stesso metodo su un altro dispositivo. Il risultato del lavoro è in
[`PROTOCOL.md`](../PROTOCOL.md) e in `nzxt_lift_elite/protocol.py`.

NZXT non documenta il protocollo. Tutto ciò che segue viene dall'osservare
cosa invia al mouse il software ufficiale (NZXT CAM su Windows), dall'analisi
statica di quel software e da prove prudenti, quasi solo in lettura, su Linux.
Il progetto non è affiliato a NZXT.

> **Leggi la [sezione sulla sicurezza](#7-regole-di-sicurezza) prima di inviare
> qualunque cosa a un dispositivo vero.** Un comando sbagliato può lasciare il
> mouse nel suo updater o bootloader, o peggio.

## Indice

1. [Il dispositivo](#1-il-dispositivo)
2. [Strumenti](#2-strumenti)
3. [Catturare il traffico USB di NZXT CAM](#3-catturare-il-traffico-usb-di-nzxt-cam)
4. [Capire il formato dei pacchetti](#4-capire-il-formato-dei-pacchetti)
5. [Mappare i campi confrontando le catture](#5-mappare-i-campi-confrontando-le-catture)
6. [Analisi statica del software e del firmware](#6-analisi-statica-del-software-e-del-firmware)
7. [Regole di sicurezza](#7-regole-di-sicurezza)
8. [Verifica su Linux](#8-verifica-su-linux)
9. [Cosa non è ancora noto](#9-cosa-non-è-ancora-noto)
10. [Condividere catture senza esporre dati](#10-condividere-catture-senza-esporre-dati)

## 1. Il dispositivo

| | ID USB | Note |
|---|---|---|
| Mouse, via cavo USB | `1e71:2129` | espone tre interfacce HID |
| Ricevitore 2,4 GHz | `1e71:2101` | stessa interfaccia vendor e stesso formato |

Per la configurazione conta una sola interfaccia: l'**interfaccia HID
vendor-defined `MI_01`** (interfaccia numero 1). Usa report interrupt da 64 byte:

- host → dispositivo: interrupt OUT, endpoint `0x02`
- dispositivo → host: interrupt IN, endpoint `0x82`

Le altre interfacce trasportano il normale input di mouse e tastiera
(l'interfaccia tastiera è quella che il mouse usa quando un tasto è associato
a un tasto della tastiera).

Su Linux ogni interfaccia ha il suo nodo `/dev/hidrawN`. Per capire quale è quale:

```sh
lsusb -d 1e71:                      # è collegato? cavo o ricevitore?
for d in /sys/class/hidraw/hidraw*; do
    echo "$d: $(grep HID_ID "$d/device/uevent") $(basename "$(readlink -f "$d/device")")"
done
```

Il nome del dispositivo in sysfs contiene un suffisso del tipo `:1.1`;
l'interfaccia vendor è quella con numero `1` (la libreria la sceglie da sola,
vedi `nzxt_lift_elite/device.py`).

## 2. Strumenti

Tutto ciò che è stato usato è software libero o disponibile gratuitamente:

| Scopo | Strumento |
|---|---|
| Software di configurazione ufficiale (l'"oracolo") | NZXT CAM su Windows |
| Cattura USB su Windows | [USBPcap](https://desowin.org/usbpcap/) |
| Visualizzare e filtrare le catture | [Wireshark](https://www.wireshark.org/) / `tshark` |
| Script, parsing delle catture, confronti | Python 3 |
| Disassemblaggio / decompilazione | [Ghidra](https://ghidra-sre.org/) (anche headless), [Capstone](https://www.capstone-engine.org/), `pefile` |
| Prove su Linux | l'interfaccia `hidraw` del kernel, `liftctl --debug` / `--dry-run` / `debug-dump` |

Per la fase di cattura una macchina virtuale Windows con passthrough USB va
bene quanto un'installazione Windows vera.

## 3. Catturare il traffico USB di NZXT CAM

L'idea di base: **cambia una sola impostazione in CAM, cattura ciò che passa
sul cavo e confronta.**

### 3.1 Registrare

1. Installa USBPcap (è un'opzione dell'installer di Wireshark) e riavvia.
2. Collega il mouse prima **con il cavo**: è il percorso più semplice (niente
   radio, nessun ricevitore in mezzo). Ripeti poi le catture interessanti con
   il ricevitore.
3. Avvia Wireshark e scegli l'interfaccia `USBPcapN` dell'hub radice a cui è
   collegato il mouse (se non sei sicuro, cattura una volta tutte le
   interfacce e cerca il descrittore del dispositivo con vendor `0x1e71`).
4. Avvia la cattura e poi CAM: le letture all'avvio sono una miniera d'oro
   (informazioni sul dispositivo, tutti i profili, tutte le mappature dei
   tasti, il profilo attivo).
5. Fai **una** sola azione in CAM, aspetta un paio di secondi, annota l'ora e
   cosa hai fatto. Poi l'azione successiva. Catture brevi con una modifica
   ciascuna sono molto più facili da leggere di una lunga sessione.
6. Salva in `.pcap`/`.pcapng`. Le catture USBPcap usano il link type 249
   (`LINKTYPE_USBPCAP`): ogni pacchetto ha un piccolo header USBPcap (id IRP,
   stato, funzione, direzione, bus, indirizzo del dispositivo, endpoint, tipo
   di trasferimento, lunghezza dati) seguito dal payload. Quell'header si
   legge con poche righe di Python, se preferisci analizzare con uno script
   invece di cliccare in Wireshark.

Esperimenti utili, una impostazione alla volta:

- cambiare il polling rate passando per tutti i valori
- cambiare il valore di un solo stadio DPI (per esempio lo stadio 2 da 800 a 1000)
- attivare/disattivare il motion sync, cambiare la distanza di lift-off
- assegnare a un tasto, una per una e nell'ordine del menu, tutte le azioni del menu di CAM
- cambiare profilo in CAM
- riportare un profilo ai valori di fabbrica

### 3.2 Filtri di Wireshark

Prima trova l'indirizzo del dispositivo (cambia quando ricolleghi il mouse):

```
usb.idVendor == 0x1e71
```

Poi tieni solo il traffico interrupt di quel dispositivo (sostituisci `N`):

```
usb.device_address == N && usb.transfer_type == 0x01
```

Per restringere ancora:

| Filtro | Mostra |
|---|---|
| `usb.endpoint_address == 0x02` | host → mouse (comandi) |
| `usb.endpoint_address == 0x82` | mouse → host (risposte) |
| `usb.capdata[0] == 0x4e` | solo i frame vendor (iniziano tutti con `4e`) |
| `usb.capdata[0] == 0x4e && usb.capdata[4] == 0x92` | un solo comando, qui *leggi profilo* |
| `!(usb.capdata[1] == 0xe5)` | nasconde le brevi risposte "occupato" |

A seconda della versione di Wireshark e del fatto che decodifichi o meno il
report descriptor HID, il campo del payload è `usb.capdata` oppure
`usbhid.data`: usa quello che compare cliccando sui byte del payload.

Per ottenere un dump di testo da confrontare con `diff`:

```sh
tshark -r cattura.pcapng -Y 'usb.transfer_type == 0x01 && usb.capdata[0] == 0x4e' \
       -T fields -e frame.time_relative -e usb.endpoint_address -e usb.capdata
```

## 4. Capire il formato dei pacchetti

Mettendo in fila comandi e risposte emerge subito una struttura fissa. Ogni
frame vendor è lungo 64 byte, con zeri di riempimento:

```
4e TT LL 00 CMD argomenti...
```

| Byte | Significato |
|---|---|
| 0 | `0x4e`, valore magico |
| 1 | destinatario: `02` = mouse, `01` = ricevitore |
| 2 | `0x80 \| n`, dove `n` = numero di byte dopo il byte 3 (comando + argomenti) |
| 3 | `00` per i messaggi in un solo frame |
| 4 | byte di comando |
| 5… | argomenti |

La regola della lunghezza è stata ricavata dalle catture e vale per ogni
pacchetto visto: `81` per un comando senza argomenti, `82` con un argomento,
`85` per la scrittura di un tasto (5 byte), `94` per la scrittura di un
profilo intero.

Le risposte usano lo stesso header. Il primo byte del payload è uno stato
(`01` = OK) per quasi tutti i comandi; fa eccezione il comando di
informazioni `0b`, i cui dati iniziano subito. Spesso il dispositivo risponde
prima con un breve frame `4e e5 …`, che significa "occupato / ricevuto": va
ignorato e si legge di nuovo.

Lo schema è rigorosamente **richiesta → risposta**: l'host invia un report
OUT e aspetta il report IN corrispondente. La libreria fa lo stesso e non
accoda mai più comandi.

## 5. Mappare i campi confrontando le catture

Noto il formato, ogni esperimento diventa un piccolo confronto. Alcuni esempi
di come sono stati identificati i campi:

**Profili (`92` lettura, `93` scrittura).** All'avvio CAM invia
`4e 02 82 00 92 NN` per `NN` = 0…4 e riceve `4e 02 94 00 01 NN` seguito dal
corpo del profilo. La scrittura è lo stesso corpo inviato con il comando `93`.
Confrontando i corpi prima e dopo una modifica in CAM:

- polling rate → cambia il byte 6 (`0`=250, `1`=500, `2`=1000, `3`=2000,
  `4`=4000, `5`=8000 Hz)
- stadio DPI 2 da 800 a 1000 → i byte 12–13 diventano `e8 03`, quindi i
  valori DPI sono cinque `uint16` little-endian ai byte 10–19
- motion sync → byte 23 (`1` attivo, `0` spento)
- lift-off → byte 7 (visti i valori 1 e 2)

**Stadio DPI attivo (byte 9).** Le sole catture non permettevano di capire se
il byte partisse da 0 o da 1. Si è chiarito su Linux con sole letture:
premendo il tasto DPI e rileggendo il profilo dopo ogni pressione si è
ottenuto `0, 2, 3, 4`, poi di nuovo `0`, e a `0` il puntatore era al primo
valore DPI (il più basso). Il byte quindi **parte da 0**; gli strumenti lo
mostrano come 1–5.

**Tasti (`51` lettura, `50` scrittura).** All'avvio CAM legge tutti i tasti
di tutti i profili con `4e 02 84 00 51 PP BB 00` e scrive con
`4e 02 85 00 50 PP BB 04 AA`. Assegnando allo stesso tasto, in ordine, ogni
voce del menu azioni di CAM si è ottenuta la tabella delle azioni di
`PROTOCOL.md`: `a5`…`aa` sono azioni del mouse, gli altri valori sono codici
HID della tastiera (i tasti multimediali usano i vecchi codici della pagina
tastiera `7f`–`81` ed `e8`–`eb`).

**Colore del LED del profilo (byte 21–22).** Questi due byte sono diversi per
ogni profilo di fabbrica: `1f00`, `00f8`, `e007`, `40fd`, `1ff8`. Letti come
valori a 16 bit little-endian sono `0x001f`, `0xf800`, `0x07e0`, `0xfd40`,
`0xf81f`, cioè esattamente blu, rosso, verde, giallo e viola in RGB565: i
colori dei profili indicati nel manuale ufficiale. Il colore si vede per circa
due secondi quando tieni premuto a lungo il tasto viola per cambiare profilo.
CAM non ha un editor del colore, quindi non esiste una cattura di un cambio
di colore; se un valore personalizzato compaia in quel lampeggio è ancora in
prova.

**Profilo attivo (`44` lettura, `45` impostazione).** All'avvio CAM invia
`4e 02 81 00 44` e riceve `4e 02 82 00 01 PP`; invia anche
`4e 02 82 00 45 PP` (risposta `4e 02 81 00 01`). I nomi vengono da CAM stesso
(`CMD_GET_PROFILE_NUMBER` / `CMD_SET_PROFILE_NUMBER`, vedi §6) e il gestore
nel firmware ne conferma il significato. La lettura è attiva negli strumenti;
l'impostazione è implementata ma disattivata finché non viene confermata sul
dispositivo vero.

**Ciò che *non* è mappato** resta come byte grezzi e viene riscritto
invariato (vedi §7).

## 6. Analisi statica del software e del firmware

Le catture mostrano *cosa* viene inviato; l'analisi statica aiuta a capire
*perché*, dà un nome ai comandi e rivela comandi che CAM invia solo in
situazioni rare.

**Il software host.** La logica dei dispositivi di CAM sta in un modulo
nativo Node.js, un PE Windows x64 scritto in Rust. Buoni punti di partenza:

- già `strings` mostra i nomi dei comandi (`CMD_GET_PROFILE_NUMBER`,
  `CMD_SET_PROFILE_NUMBER`, `CMD_GET_ACTIVE_CPI_INDEX`, …) e i percorsi dei
  sorgenti Rust delle classi di dispositivo, che dicono quale codice gestisce
  questo mouse
- la tabella delle eccezioni del PE (`.pdata`) dà confini delle funzioni
  affidabili, molto utile con il codice Rust
- i metadati di logging/tracing contengono posizioni `file.rs:riga`;
  annotare i punti di chiamata con queste posizioni rende il codice
  decompilato molto più leggibile
- le macchine a stati `async` di Rust si decompilano male di default (Ghidra
  segnala un numero di istruzioni errato); correggere a mano i limiti delle
  jump table risolve
- la funzione che costruisce il frame `4e …` è un ottimo punto d'appoggio:
  ogni funzione di comando la chiama con un byte di comando costante

**Il firmware.** Il mouse usa un Nordic nRF52840 (ARM Cortex-M4, Thumb,
FreeRTOS). Le immagini del firmware sono incluse in CAM per il suo updater.
Sono proprietarie e **non** sono contenute in questo repository né linkate da
esso. Se analizzi una tua copia:

- carica l'immagine grezza come `ARM:LE:32:Cortex` al suo indirizzo base
  corretto; il reset vector nella tabella dei vettori (seconda word) ti dice
  se la base è giusta
- trova il **dispatcher dei comandi**: un grande `switch` sul byte di comando
  del report in arrivo. I comandi già confermati dalle catture (`92`, `93`,
  `50`, `51`) sono i punti d'appoggio; trovati quei casi, i casi vicini sono
  gli altri comandi
- segui cosa legge o scrive ogni caso. Per esempio il caso `44` copia nella
  risposta un indice globale "profilo corrente", lo stesso globale è il
  valore di ripiego quando `92` riceve uno slot fuori intervallo, e il caso
  `45` chiama la stessa routine di cambio profilo usata dalla pressione lunga
  del tasto viola. È così che `44`/`45` sono stati identificati con alta
  confidenza
- bastano script che trovano chi carica una costante dal literal pool e chi
  chiama una funzione con `BL` per muoversi in una piccola immagine Thumb
  senza simboli

Le scoperte dell'analisi statica sono trattate come **ipotesi** finché una
cattura o una lettura sicura sul dispositivo vero non le conferma.
`PROTOCOL.md` indica quali campi sono confermati e quali no.

## 7. Regole di sicurezza

Queste regole sono integrate nella libreria e chiunque faccia esperimenti
dovrebbe seguirle:

1. **Mai inviare comandi sconosciuti.** Niente fuzzing del byte di comando.
   La libreria ha una lista di comandi ammessi (`SAFE_COMMANDS` in
   `protocol.py`) e rifiuta tutto il resto prima che arrivi al dispositivo.
2. **Leggi prima di scrivere.** Inizia con i comandi di sola lettura (`0b`
   informazioni, `92` leggi profilo, `51` leggi tasto, `44` profilo attivo,
   `b0` stato). `--dry-run` stampa i pacchetti che *verrebbero* scritti senza
   inviarli.
3. **Prima fai un backup.** Prima di ogni scrittura gli strumenti salvano una
   copia di tutti e cinque i profili (`~/.config/nzxt-lift-elite/backups/`,
   tenuti gli ultimi 50).
4. **Cambia solo ciò che capisci.** Le scritture partono dai byte appena letti
   dal dispositivo e modificano solo i campi decodificati; i byte sconosciuti
   vengono riscritti come erano.
5. **Verifica ogni scrittura** rileggendo e confrontando.
6. **Prova sull'emulatore** (`nzxt_lift_elite/mock.py`, `--mock`) prima di
   toccare l'hardware.
7. **Tieni una configurazione funzionante** da poter ripristinare
   (`liftctl save --all`).

### Comandi di aggiornamento firmware e bootloader

Il firmware contiene comandi per cambiare modalità di avvio, riavviare,
cancellare, scrivere e verificare la flash. **Possono rendere il mouse
inutilizzabile.** Qui sono volutamente **non documentati nel dettaglio**.
Nella libreria esistono solo come costanti `DANGEROUS_*`, per poterli
riconoscere e rifiutare; nulla nel CLI, nella GUI o nel plugin CoolerControl
può inviarli, e non esiste alcuna routine di flashing. Se trovi uno di questi
byte di comando in una cattura, non riprodurlo.

## 8. Verifica su Linux

Installa la regola udev come spiegato nel [README](../README.it.md), così puoi
aprire il nodo hidraw da utente normale, poi:

```sh
liftctl devices                 # cosa è collegato (solo sysfs, non apre nulla)
liftctl firmware-info           # sola lettura: comando 0b
liftctl --debug show            # sola lettura, dump esadecimale di ogni pacchetto su stderr
liftctl debug-dump --out a.txt  # sola lettura: tutte le letture note, in esadecimale
```

`debug-dump` è lo strumento per confermare un campo: eseguilo, cambia
qualcosa *sul mouse stesso* (premi il tasto DPI, tieni premuto a lungo il
tasto viola), eseguilo di nuovo con `--out b.txt` e fai `diff a.txt b.txt`.

Se vuoi sperimentare in Python, questo è l'intero scambio per il comando di
informazioni, di sola lettura (adatta il nodo; la libreria aggiunge lock,
tentativi ripetuti e controlli di sicurezza):

```python
import os, select

frame = bytes([0x4e, 0x02, 0x81, 0x00, 0x0b]).ljust(64, b"\0")
fd = os.open("/dev/hidrawN", os.O_RDWR)
# Se il report descriptor di questa interfaccia non dichiara 0x4e come
# report ID, hidraw vuole un report ID 0 iniziale: scrivi b"\0" + frame.
os.write(fd, frame)
while select.select([fd], [], [], 1.0)[0]:
    reply = os.read(fd, 72)
    if reply[:1] == b"\0":
        reply = reply[1:]         # togli il report ID 0, se presente
    if reply[1] != 0xe5:          # salta i frame "occupato"
        print(reply[:64].hex(" "))
        break
os.close(fd)
```

La libreria legge il report descriptor da sysfs e decide da sola se serve il
byte del report ID.

Per tutto ciò che va oltre le letture usa la libreria
(`from nzxt_lift_elite import LiftElite`) invece di scritture grezze: applica
la lista dei comandi ammessi.

## 9. Cosa non è ancora noto

- **Impostazione del profilo attivo (`45`)**: pacchetto noto, routine del
  firmware nota, non ancora confermato sul dispositivo, quindi disattivato.
- **Colori LED personalizzati**: un valore RGB565 personalizzato viene salvato
  e si rilegge; se il lampeggio al cambio profilo lo mostri è in prova.
- **Stato `b0`**: non decodificato. Due byte sembrano una tensione della
  batteria in millivolt e `100` compare due volte, forse una percentuale. Non
  esposto finché non è confermato (per esempio vedendolo scendere a batteria).
- **Ricevitore**: si assume che i comandi per il mouse (destinatario `02`)
  vengano inoltrati via radio; la lettura dei tasti e le versioni del
  ricevitore sono state catturate via cavo.
- Il byte 20 del profilo (`0x0f`, probabilmente una maschera degli stadi
  abilitati) e la corrispondenza esatta in millimetri del lift-off.

Le catture che chiariscono uno di questi punti sono benvenute.

## 10. Condividere catture senza esporre dati

Le catture USB possono contenere più di quanto pensi. Prima di condividerne una:

- le risposte a `03` e `b8` contengono un **identificativo unico del
  dispositivo**: oscura quei byte
- una cattura di tutto il bus contiene anche ogni altro dispositivo su
  quell'hub (e qualunque cosa digitata su una tastiera USB): cattura solo
  l'hub del mouse, oppure filtra ed esporta solo i pacchetti del mouse
  (*File → Export Specified Packets*)
- rinomina i file in modo che non contengano nomi utente o nomi del computer
- non condividere mai immagini del firmware o binari di CAM: sono proprietà di NZXT
