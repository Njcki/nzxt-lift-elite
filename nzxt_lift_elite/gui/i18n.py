"""Tiny i18n layer: English msgids, one dict per language.

The UI language is Italian unless ``LIFT_ELITE_LANG`` says otherwise
(``en`` shows the msgids). To add a language, add a dict to ``CATALOGS``.
"""

from __future__ import annotations

import os

IT: dict[str, str] = {
    # window / header
    "Lift Elite Control": "Lift Elite Control",
    "Refresh": "Aggiorna",
    "Read the mouse again": "Rileggi il mouse",
    "Apply": "Applica",
    "Write the changes to the mouse": "Scrivi le modifiche sul mouse",
    "Discard": "Annulla modifiche",
    "Discard unsaved changes": "Annulla le modifiche non applicate",
    "Main menu": "Menu principale",
    "Save as named profile…": "Salva come profilo con nome…",
    "Load named profile…": "Carica profilo con nome…",
    "Import from file…": "Importa da file…",
    "Export this profile…": "Esporta questo profilo…",
    "Export all profiles…": "Esporta tutti i profili…",
    "Factory buttons for this profile": "Tasti di fabbrica per questo profilo",
    "Appearance": "Aspetto",
    "Follow system": "Segui il sistema",
    "Light": "Chiaro",
    "Dark": "Scuro",
    "About Lift Elite Control": "Informazioni su Lift Elite Control",
    "Unofficial configuration tool for the NZXT Lift Elite mouse. Not affiliated with NZXT.":
        "Strumento non ufficiale per configurare il mouse NZXT Lift Elite. Non affiliato a NZXT.",
    # states
    "Looking for the mouse…": "Cerco il mouse…",
    "Reading settings…": "Leggo le impostazioni…",
    "Mouse not found": "Mouse non trovato",
    "Disconnected": "Scollegato",
    "Mouse disconnected": "Mouse scollegato",
    "unsaved changes were kept": "le modifiche non salvate sono state conservate",
    "Mouse connected ({transport})": "Mouse collegato ({transport})",
    "Switched to {transport}": "Passato a {transport}",
    "Re-read the mouse. Unsaved changes were kept.": "Mouse riletto. Le modifiche non salvate sono state conservate.",
    "The mouse is disconnected. Changes will be written when you reconnect and press Apply.":
        "Il mouse è scollegato. Le modifiche verranno scritte quando lo ricolleghi e premi Applica.",
    "Connect the NZXT Lift Elite with the cable or plug in its receiver. The app notices it automatically.":
        "Collega l'NZXT Lift Elite col cavo o inserisci il ricevitore. L'app se ne accorge da sola.",
    "Connect the NZXT Lift Elite with the cable or plug in its receiver, then try again.":
        "Collega l'NZXT Lift Elite con il cavo o inserisci il ricevitore, poi riprova.",
    "Try again": "Riprova",
    "Demo mode": "Modalità demo",
    "Demo (emulated mouse)": "Demo (mouse emulato)",
    "Nothing is written on startup. Changes are sent only when you press Apply.":
        "All'avvio non viene scritto niente: le modifiche partono solo quando premi Applica.",
    # device panel
    "Device": "Dispositivo",
    "Connection": "Collegamento",
    "Cable": "Via cavo",
    "Wireless (receiver)": "Wireless (ricevitore)",
    "Mouse firmware": "Firmware mouse",
    "Updater": "Updater",
    "Bridge": "Bridge",
    "Receiver firmware": "Firmware ricevitore",
    "Onboard profile": "Profilo nel mouse",
    "Slot {slot} in liftctl · LED {color}": "Slot {slot} in liftctl · LED {color}",
    "Profile {n}": "Profilo {n}",
    "Profile {n} (active on the mouse)": "Profilo {n} (attivo sul mouse)",
    "Scegli il profilo da modificare e da attivare sul mouse con Applica.": "Scegli il profilo da modificare e da attivare sul mouse con Applica.",
    "si vede per ~2 s quando cambi profilo tenendo premuto il tasto viola":
        "si vede per ~2 s quando cambi profilo tenendo premuto il tasto viola",
    "Scegli il profilo da modificare. Per attivarlo sul mouse tieni premuto a lungo il tasto viola.":
        "Scegli il profilo da modificare. Per attivarlo sul mouse tieni premuto a lungo il tasto viola.",
    "Writing…": "Scrittura…",
    "Underside": "Sotto",
    "modified": "modificato",
    "Click a button on the mouse to remap it": "Clicca un tasto sul mouse per rimapparlo",
    # performance
    "Performance": "Prestazioni",
    "Polling rate": "Frequenza di polling",
    "Lift-off distance": "Distanza di lift-off",
    "Motion sync": "Motion sync",
    "Syncs sensor frames with USB reports (slightly smoother, tiny extra latency)":
        "Sincronizza il sensore con i report USB (più regolare, latenza minima in più)",
    "Profile LED colour": "Colore LED del profilo",
    "Shown when you switch profile (long press on the purple button). Stored as RGB565.":
        "Si vede quando cambi profilo (pressione lunga sul tasto viola). Salvato in RGB565.",
    "Shown for about two seconds when you long-press the purple button to change profile. During normal use the LED is a status light (green while charging or charged).":
        "Si vede per circa due secondi quando tieni premuto il tasto viola per cambiare profilo. Nell'uso normale il LED è una spia di stato (verde in carica o a carica completa).",
    "Choose the LED colour": "Scegli il colore del LED",
    "{hz} Hz": "{hz} Hz",
    "{mm} mm": "{mm} mm",
    # dpi
    "DPI stages": "Stadi DPI",
    "The purple button under the mouse cycles through these five values. The selected stage is the one stored as active.":
        "Il tasto viola sotto il mouse cicla questi cinque valori. Lo stadio selezionato è quello salvato come attivo.",
    "Stage {n}": "Stadio {n}",
    "active": "attivo",
    "Make this the active stage": "Rendi attivo questo stadio",
    # buttons
    "Buttons": "Tasti",
    "Choose what each button does in this profile. Media keys work on Linux without drivers.":
        "Scegli cosa fa ogni tasto in questo profilo. I tasti multimediali funzionano su Linux senza driver.",
    "Left button": "Tasto sinistro",
    "Middle button (wheel)": "Tasto centrale (rotella)",
    "Right button": "Tasto destro",
    "Side forward": "Laterale avanti",
    "Side back": "Laterale indietro",
    "DPI button": "Tasto DPI",
    "Choose action": "Scegli l'azione",
    "Action for: {button}": "Azione per: {button}",
    "Search actions": "Cerca un'azione",
    "Type a name, a key or a code (e.g. volume, F5, 0x04)": "Scrivi un nome, un tasto o un codice (es. volume, F5, 0x04)",
    "No action found": "Nessuna azione trovata",
    "Default": "Predefinito",
    "Restore the default action": "Ripristina l'azione predefinita",
    "Unknown code 0x{code}": "Codice sconosciuto 0x{code}",
    # categories
    "Mouse": "Mouse",
    "Media": "Multimedia",
    "Letters": "Lettere",
    "Digits": "Numeri",
    "Function keys": "Tasti funzione",
    "Modifiers": "Modificatori",
    "Navigation": "Navigazione",
    "Editing": "Modifica",
    "Symbols": "Simboli",
    "Keypad": "Tastierino numerico",
    "System": "Sistema",
    "International": "Internazionali",
    "Other": "Altro",
    # action labels
    "Left click": "Clic sinistro",
    "Middle click": "Clic centrale",
    "Right click": "Clic destro",
    "Mouse forward": "Mouse avanti",
    "Mouse back": "Mouse indietro",
    "DPI cycle": "Cambio DPI",
    "Mute": "Muto",
    "Volume up": "Volume su",
    "Volume down": "Volume giù",
    "Play / Pause": "Play / Pausa",
    "Stop": "Stop",
    "Previous track": "Traccia precedente",
    "Next track": "Traccia successiva",
    "Enter": "Invio",
    "Space": "Spazio",
    "Caps Lock": "Bloc Maiusc",
    "Print Screen": "Stamp",
    "Scroll Lock": "Bloc Scorr",
    "Pause": "Pausa",
    "Insert": "Ins",
    "Home": "Inizio",
    "Page Up": "Pagina su",
    "Delete": "Canc",
    "End": "Fine",
    "Page Down": "Pagina giù",
    "Right arrow": "Freccia destra",
    "Left arrow": "Freccia sinistra",
    "Down arrow": "Freccia giù",
    "Up arrow": "Freccia su",
    "Num Lock": "Bloc Num",
    "Keypad Enter": "Invio (tastierino)",
    "Menu (Application)": "Menu contestuale",
    "Power": "Spegnimento",
    "Help": "Aiuto",
    "Again": "Ripeti",
    "Undo": "Annulla",
    "Cut": "Taglia",
    "Copy": "Copia",
    "Paste": "Incolla",
    "Find": "Trova",
    "Ctrl": "Ctrl",
    "Shift": "Maiusc",
    "Alt": "Alt",
    "Left Super (Windows)": "Windows sinistro (Super)",
    "Right Ctrl": "Ctrl destro",
    "Right Shift": "Maiusc destro",
    "AltGr": "Alt Gr",
    "Right Super (Windows)": "Windows destro (Super)",
    "Stop (keyboard)": "Stop (tastiera)",
    "Thousands separator": "Separatore migliaia",
    "Decimal separator": "Separatore decimale",
    "Currency unit": "Unità di valuta",
    "Currency sub-unit": "Sottounità di valuta",
    # apply / dialogs
    "Write to the mouse?": "Scrivere sul mouse?",
    "These changes will be written to the onboard memory and read back to verify them:":
        "Queste modifiche verranno scritte nella memoria del mouse e rilette per verificarle:",
    "Cancel": "Annulla",
    "Write": "Scrivi",
    "Applied and verified": "Applicato e verificato",
    "No changes to apply": "Nessuna modifica da applicare",
    "Write failed": "Scrittura non riuscita",
    "Read failed": "Lettura non riuscita",
    "Changes discarded": "Modifiche annullate",
    "Unapplied changes": "Modifiche non applicate",
    "Some changes were not written to the mouse. Quit anyway?":
        "Alcune modifiche non sono state scritte sul mouse. Uscire comunque?",
    "Quit": "Esci",
    "Profile name": "Nome del profilo",
    "Save": "Salva",
    "Saved “{name}”": "Salvato «{name}»",
    "Loaded “{name}” into profile {n} — press Apply to write it":
        "Caricato «{name}» nel profilo {n}: premi Applica per scriverlo",
    "Named profiles": "Profili con nome",
    "Stored in {path}": "Salvati in {path}",
    "No named profiles yet": "Ancora nessun profilo con nome",
    "Load": "Carica",
    "Delete profile": "Elimina profilo",
    "Deleted “{name}”": "Eliminato «{name}»",
    "Exported to {path}": "Esportato in {path}",
    "Imported {name} — press Apply to write it": "Importato {name}: premi Applica per scriverlo",
    "Import failed": "Importazione non riuscita",
    "JSON profiles": "Profili JSON",
    "Backup of the previous settings: {path}": "Backup delle impostazioni precedenti: {path}",
    "Factory buttons set — press Apply to write them": "Tasti di fabbrica impostati: premi Applica per scriverli",
    "Polling": "Polling",
    "Lift-off": "Lift-off",
    "LED colour": "Colore LED",
    "Active stage": "Stadio attivo",
    "on": "attivo",
    "off": "disattivo",
    "Close": "Chiudi",
    "Details": "Dettagli",
}

CATALOGS: dict[str, dict[str, str]] = {"it": IT, "en": {}}


def detect_language() -> str:
    lang = os.environ.get("LIFT_ELITE_LANG", "").strip().lower()
    if lang:
        lang = lang.split("_")[0].split(".")[0]
        return lang if lang in CATALOGS else "it"
    return "it"


_LANG = detect_language()


def set_language(lang: str) -> None:
    global _LANG
    _LANG = lang if lang in CATALOGS else "it"


def language() -> str:
    return _LANG


def _(msgid: str, **kwargs: object) -> str:
    text = CATALOGS.get(_LANG, {}).get(msgid, msgid)
    return text.format(**kwargs) if kwargs else text


gettext = _
