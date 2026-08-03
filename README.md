# Voice Anonymizer

Script Python per alterare la voce in tempo reale, pensato per proteggere
l'impronta vocale durante riunioni (es. Microsoft Teams) che vengono
registrate e/o trascritte automaticamente.

Legge l'audio dal microfono reale, lo altera (pitch-shift + filtri +
chorus leggero) e lo scrive su un **microfono virtuale**, che poi viene
selezionato come input in Teams (o in qualsiasi altra app di
videoconferenza). L'alterazione può essere attivata/disattivata a
runtime senza riavviare nulla.

Funziona su **Windows** e **Ubuntu/Linux**.

## Requisiti

- Python 3.10+
- [pedalboard](https://github.com/spotify/pedalboard) (catena di effetti
  DSP: pitch-shift, filtri, chorus)
- [sounddevice](https://python-sounddevice.readthedocs.io/) (I/O audio
  in tempo reale, basato su PortAudio)
- `numpy`
- `tkinter` per l'interfaccia grafica (incluso nella maggior parte delle
  installazioni Python; su Ubuntu potrebbe servire installarlo a parte)
- Un dispositivo audio virtuale (vedi sezione [Setup](#setup-del-microfono-virtuale))

```bash
pip install pedalboard sounddevice numpy

# Solo su Ubuntu/Debian, se tkinter non è già presente:
sudo apt install python3-tk
```

> **Nota tecnica**: lo script usa `sounddevice` per aprire i flussi
> audio di input/output e `pedalboard` solo per applicare gli effetti
> ad ogni blocco audio. In una versione precedente usavamo
> `pedalboard.io.AudioStream` anche per l'I/O, ma quell'API ha un bug
> noto su Windows che a volte confonde i driver WASAPI/DirectSound,
> facendo sparire o duplicare alcuni dispositivi (in particolare
> headset USB/Bluetooth) — vedi
> [spotify/pedalboard#274](https://github.com/spotify/pedalboard/issues/274).
> `sounddevice` si è dimostrato più affidabile nell'enumerazione dei
> dispositivi.

## Uso — Interfaccia grafica (consigliata)

Avvia lo script senza argomenti (o con `--gui`) per aprire l'interfaccia:

```bash
python voice_anonymizer.py
```

Nella finestra puoi:

- selezionare il **microfono reale** (input) e il **dispositivo virtuale**
  (output) dai menu a tendina;
- regolare il **pitch-shift** in semitoni con lo slider;
- premere **Avvia** per aprire lo stream audio;
- usare il **piccolo interruttore** "Alterazione vocale" per attivare o
  disattivare l'effetto in qualsiasi momento, senza fermare lo stream
  (utile se in certi momenti della riunione vuoi parlare con la voce
  originale).

Lo stato corrente (stream avviato/fermato, alterazione attiva/disattiva)
è sempre visibile in basso nella finestra.

## Uso — Riga di comando

1. Elenca i dispositivi audio disponibili sul tuo sistema:

   ```bash
   python voice_anonymizer.py --list-devices
   ```

2. Avvia lo script indicando il tuo microfono reale come input e il
   dispositivo virtuale come output (puoi usare il nome completo, un
   nome parziale case-insensitive, oppure l'indice numerico mostrato
   da `--list-devices`):

   ```bash
   python voice_anonymizer.py --input "Jabra" --output "CABLE Input"
   # oppure con gli indici numerici
   python voice_anonymizer.py --input 3 --output 7
   ```

3. In Teams, imposta il microfono sul dispositivo virtuale (vedi sotto).

### Comandi da terminale durante l'esecuzione (modalità CLI)

| Comando | Effetto |
|---|---|
| `t` + invio | Attiva/disattiva l'alterazione vocale (bypass ↔ voce alterata) |
| `q` + invio | Esce dal programma |

### Parametri opzionali

| Argomento | Default | Descrizione |
|---|---|---|
| `--gui` | — | Forza l'apertura dell'interfaccia grafica |
| `--diagnose` | — | Diagnostica dettagliata dei dispositivi audio ed esce (vedi [Risoluzione problemi](#risoluzione-problemi)) |
| `--semitones` | `-4.0` | Pitch-shift in semitoni. Negativo = voce più grave, positivo = più acuta |
| `--chorus-mix` | `0.2` | Intensità del chorus (0.0–1.0), aggiunge variazione timbrica |
| `--samplerate` | `48000` | Frequenza di campionamento (solo modalità CLI) |
| `--buffer-size` | `512` | Dimensione buffer audio: più basso = meno latenza, più rischio glitch (solo modalità CLI) |
| `--start-disabled` | off | Avvia con l'alterazione già disattivata (bypass, solo modalità CLI) |

> Nota: se avvii lo script senza passare `--input` e `--output`, si apre
> automaticamente la GUI, dove potrai selezionare i dispositivi dai menu
> a tendina invece che da riga di comando.

## Setup del microfono virtuale

Lo script non si collega direttamente a Teams (nessuna app di terze
parti lo permette): serve un dispositivo audio virtuale che faccia da
tramite.

### Windows

1. Installa [VB-CABLE](https://vb-audio.com/Cable/) (gratuito). Crea
   due dispositivi: `CABLE Input` (output) e `CABLE Output` (input).
2. Avvia lo script con:
   ```bash
   python voice_anonymizer.py --input "Nome del tuo microfono fisico" --output "CABLE Input (VB-Audio Virtual Cable)"
   ```
3. In Teams: **Impostazioni → Dispositivi → Microfono** = `CABLE Output (VB-Audio Virtual Cable)`.

### Ubuntu / Linux (PulseAudio o PipeWire-Pulse)

1. Crea un sink virtuale:
   ```bash
   pactl load-module module-null-sink sink_name=VoiceAnonymizer_Sink sink_properties=device.description=VoiceAnonymizer_Sink
   ```
2. Avvia lo script con:
   ```bash
   python voice_anonymizer.py --input "il tuo microfono reale (es. default)" --output "VoiceAnonymizer_Sink"
   ```
3. In Teams: **Impostazioni → Dispositivi → Microfono** = `Monitor of VoiceAnonymizer_Sink`
   (visibile/rinominabile anche da `pavucontrol`, scheda *Recording*/*Input Devices*).

## Come funziona l'alterazione

La catena di effetti applicata (quando attiva) è:

1. **Pitch-shift**: cambia l'intonazione di base, mascherando il
   fondamentale della voce (la caratteristica più identificativa
   dell'impronta vocale).
2. **Filtro passa-alto/passa-basso**: restringe leggermente la banda,
   spostando il timbro percepito.
3. **Chorus leggero**: introduce micro-variazioni che rendono più
   difficile il riconoscimento del parlante da parte di sistemi di
   speaker recognition, mantenendo il parlato intelligibile per la
   trascrizione automatica (STT).

Non è un sistema di anonimizzazione vocale certificato o
crittograficamente sicuro: riduce la riconoscibilità della voce ma non
garantisce l'impossibilità assoluta di identificazione (es. tramite
analisi forense avanzata). Va considerato uno strumento di mitigazione
pratica, non una garanzia formale di anonimato.

## Gestione delle dipendenze con ambiente virtuale

È consigliabile installare le dipendenze in un **ambiente virtuale**
(venv), per non "sporcare" l'installazione Python di sistema e per
poter riprodurre facilmente lo stesso setup su un'altra macchina.

### Creazione e attivazione

**Ubuntu / Linux:**
```bash
# Crea l'ambiente virtuale (una sola volta)
python3 -m venv venv

# Attivalo (ad ogni nuova sessione di terminale)
source venv/bin/activate
```

**Windows (PowerShell):**
```powershell
# Crea l'ambiente virtuale (una sola volta)
python -m venv venv

# Attivalo (ad ogni nuova sessione di terminale)
.\venv\Scripts\Activate.ps1
```

**Windows (prompt dei comandi / cmd.exe):**
```cmd
python -m venv venv
venv\Scripts\activate.bat
```

Quando l'ambiente è attivo, il prompt del terminale mostra il prefisso
`(venv)`. Da quel momento in poi, `python` e `pip` puntano
all'ambiente virtuale e non a quello di sistema.

### Installazione delle dipendenze

Con l'ambiente virtuale attivo:

```bash
pip install pedalboard sounddevice numpy
```

Su Ubuntu, se serve anche `tkinter` per la GUI e non è già presente,
va installato a livello di sistema (non nel venv, perché è un modulo
legato all'interprete Python di sistema):
```bash
sudo apt install python3-tk
```

### File dei requisiti (opzionale ma consigliato)

Per rendere il progetto facilmente riproducibile, crea un file
`requirements.txt` nella cartella del progetto:
```
pedalboard>=0.9
sounddevice>=0.4
numpy
```

e installa tutto con un solo comando:
```bash
pip install -r requirements.txt
```

Per "congelare" le versioni esatte effettivamente installate (utile
quando condividi il repo o lo porti su un'altra macchina):
```bash
pip freeze > requirements.txt
```

### Disattivare l'ambiente virtuale

Quando hai finito:
```bash
deactivate
```

### Eseguire lo script con l'ambiente virtuale attivo

```bash
source venv/bin/activate      # Linux, oppure .\venv\Scripts\Activate.ps1 su Windows
python voice_anonymizer.py
```

## Risoluzione problemi

### Un dispositivo (es. cuffie Jabra) non compare come microfono di input

**Aggiornamento**: dalla versione attuale lo script usa `sounddevice`
per l'intera gestione dei dispositivi audio (non solo per la
diagnostica), proprio perché più affidabile di `pedalboard.io.AudioStream`
su Windows con dispositivi USB/Bluetooth (vedi nota nella sezione
[Requisiti](#requisiti)). Se il tuo Jabra risultava visibile solo come
output con la versione precedente dello script, ora dovrebbe comparire
correttamente anche come input.

Se un dispositivo continua a non comparire come input, lancia comunque
la diagnostica:

```bash
python voice_anonymizer.py --diagnose
```

Questo comando mostra una tabella con **tutti** i dispositivi audio
visti dal sistema, il numero di canali di input/output di ciascuno e
l'host API associata (WASAPI, MME, PulseAudio, ecc.), utile per capire
se il microfono è davvero disponibile, con quale nome esatto e sotto
quale host API (preferisci sempre la riga con hostapi "Windows WASAPI"
per dispositivi USB/Bluetooth, se presente).

Le cause più comuni se il dispositivo non risulta disponibile **a
nessun livello**:

- **Bluetooth in profilo A2DP invece di HFP/HSP**: il profilo A2DP dà
  solo audio in uscita di alta qualità, *senza* microfono. Per avere
  l'input serve passare al profilo Headset/Handsfree (qualità audio in
  uscita inferiore, ma con microfono disponibile).
  - *Ubuntu*: apri `pavucontrol` → scheda **Configuration** → seleziona
    il profilo "Headset Head Unit (HSP/HFP)" per il dispositivo.
  - *Windows*: di solito il profilo cambia automaticamente quando
    un'app richiede il microfono; se non succede, verificalo in
    **Impostazioni → Bluetooth e dispositivi → [il tuo dispositivo] →
    altre opzioni**, oppure disabilita/riabilita il dispositivo.

- **Dispositivo occupato in esclusiva** da un'altra applicazione (anche
  Teams stesso, se già aperto): chiudi le altre app che potrebbero
  tenere il microfono impegnato e riprova.

- **Verifica a livello di sistema operativo**, indipendentemente dallo
  script:
  - *Windows*: **Impostazioni → Sistema → Suono → Ingresso**, controlla
    che il microfono sia elencato, abilitato e non disattivato.
  - *Ubuntu*: `pactl list sources short` oppure `pavucontrol` (scheda
    **Input Devices**), per verificare che la sorgente microfono esista
    a livello di sistema prima ancora di provare con lo script.

## Licenza

Questo script usa [`pedalboard`](https://github.com/spotify/pedalboard),
che è concesso in licenza **GPLv3** (include codice da JUCE 6, anch'esso
dual-licensed commerciale/GPLv3).

Di conseguenza, **questo repository è distribuito sotto licenza GPLv3**
(vedi file `LICENSE`), non MIT: uno script che importa direttamente una
libreria GPLv3 e viene distribuito pubblicamente eredita, nella pratica,
gli obblighi copyleft della GPLv3.

Se ti serve una licenza permissiva (MIT/BSD) per riuso in progetti
commerciali chiusi, è necessario sostituire `pedalboard` con librerie a
licenza permissiva (es. `sounddevice` + un pitch-shifter scritto con
`numpy`/`scipy`) ed evitare qualunque dipendenza GPL. Questa nota non
costituisce consulenza legale: per un caso con implicazioni reali,
verifica con un legale specializzato in licenze open source.

## Disclaimer

Questo strumento è pensato per proteggere i propri dati biometrici
vocali durante riunioni registrate/trascritte. In molti contesti
(aziendali o normativi) è buona prassi informare gli altri partecipanti
che si sta usando un'alterazione vocale durante la registrazione.