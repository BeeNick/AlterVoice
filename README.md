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
- [pedalboard](https://github.com/spotify/pedalboard) (gestisce l'I/O
  audio in tempo reale e gli effetti DSP)
- Un dispositivo audio virtuale (vedi sezione [Setup](#setup-del-microfono-virtuale))

```bash
pip install pedalboard
```

## Uso

1. Elenca i dispositivi audio disponibili sul tuo sistema:

   ```bash
   python voice_anonymizer.py --list-devices
   ```

2. Avvia lo script indicando il tuo microfono reale come input e il
   dispositivo virtuale come output:

   ```bash
   python voice_anonymizer.py --input "NOME_MICROFONO_REALE" --output "NOME_DISPOSITIVO_VIRTUALE"
   ```

3. In Teams, imposta il microfono sul dispositivo virtuale (vedi sotto).

### Comandi da terminale durante l'esecuzione

| Comando | Effetto |
|---|---|
| `t` + invio | Attiva/disattiva l'alterazione vocale (bypass ↔ voce alterata) |
| `q` + invio | Esce dal programma |

### Parametri opzionali

| Argomento | Default | Descrizione |
|---|---|---|
| `--semitones` | `-4.0` | Pitch-shift in semitoni. Negativo = voce più grave, positivo = più acuta |
| `--chorus-mix` | `0.2` | Intensità del chorus (0.0–1.0), aggiunge variazione timbrica |
| `--samplerate` | `48000` | Frequenza di campionamento |
| `--buffer-size` | `512` | Dimensione buffer audio: più basso = meno latenza, più rischio glitch |
| `--start-disabled` | off | Avvia con l'alterazione già disattivata (bypass) |

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