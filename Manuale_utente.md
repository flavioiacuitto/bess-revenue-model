# Manuale utente — Modello di produttività BESS (v7)

Guida pratica per usare il modello e compilare i file Excel. Non serve saper programmare:
si tocca solo il file di input, si lancia un comando, si legge il file di output.

Per le assunzioni di modellazione e il perché delle scelte fatte, vedi
`Riepilogo_modello_BESS.md` (in inglese: è il documento che tiene traccia di ogni default e
di ogni decisione presa, ed è anche dove è finito il contenuto che nella v3 stava nel foglio
`Ipotesi` dell'output). Per la guida rapida, `README.md` (anch'esso in inglese). Questo è
l'unico documento rimasto in italiano, perché è quello operativo, pensato per chi userà il
modello senza leggere il codice.

---

## 1. Che cosa fa il modello

Dati i prezzi orari del mercato del giorno prima e le caratteristiche tecniche della
batteria, il modello decide da solo, ora per ora, quando comprare energia e quando
rivenderla, e calcola quanto si guadagna. Lo fa per ogni giorno di ogni anno della vita
utile, tenendo conto del fatto che la batteria invecchia: perde capacità e perde rendimento,
secondo le curve che tu stesso gli dai.

Il risultato è un **ricavo lordo di mercato da solo arbitraggio**, espresso in €, in k€/MW
installato e in k€/MWh installato, anno per anno, per i 20 anni dal 2029 al 2048. Non è
attualizzato: è il ricavo di ciascun anno. Non è un business plan: mancano CAPEX, OPEX, tasse
e sbilanciamenti, che si aggiungono a valle (vedi §9).

**Novità della v7.** Rispetto alla v6:
- **niente attualizzazione**: spariscono tasso di sconto, ricavo attualizzato e NPV. Esce solo il ricavo di ogni anno;
- **prezzi negativi**: nelle ore con prezzo negativo la batteria può solo caricare o stare ferma, mai scaricare. Il problema resta lineare;
- **l'anno 1 è sempre il 2029** e il modello gira **sempre 20 anni** (2029-2048): spariscono i parametri `Calendar year of year 1 (COD)` e `Useful life`;
- **colonne prezzi vuote**: ogni anno lasciato vuoto prende i prezzi dell'ultimo anno compilato prima di lui;
- **niente onere sull'immissione**: resta solo quello sul prelievo;
- **due tabelle al posto di quella di degrado**: degrado della capacità (in % di targa, da 100% a scendere) e **RTE vera** dell'asset anno per anno (es. 87% a scendere). Sparisce il parametro "RTE anno 1" e la potenza non degrada più.

**Novità della v6.** Rispetto alla v4 (di cui è la base): i limiti di ciclaggio (365 cicli/anno, 2 al giorno) non sono più parametri ma costanti fisse nel codice, l'MSD non c'è (un tentativo in v5 è stato scartato: vedi `Riepilogo_modello_BESS.md` §11-bis), e le celle da compilare sono di un giallo più tenue.

**Novità della v4.** Rispetto alla versione precedente, il modello è stato deliberatamente
semplificato: tolto tutto ciò che era "overkill" per chi non programma e non può controllare
ogni riga. In sintesi: niente più foglio `Degrado` separato (ora c'è un'unica tabella
obbligatoria dentro `Parametri`), niente augmentation, niente orizzonte di ottimizzazione
configurabile, niente file prezzi esterno, niente escalation prezzi automatica. In cambio,
l'output è più snello (4 fogli invece di 7) e più completo dove conta davvero: il dettaglio
orario di SOC, energia comprata e venduta è ora disponibile per **tutti i 20 anni**, non solo
per il primo.

---

## 2. I file: cosa è cosa

### Quelli che usi tu

| File | Cos'è |
|---|---|
| `input_bess.xlsx` | **Il file che compili.** Caratteristiche della batteria, tabelle di degrado capacità e RTE, curva prezzi. Si crea con il comando `--template` (vedi §4) |
| `risultati_bess.xlsx` | **Il file che leggi.** Lo genera il modello a ogni lancio, si sovrascrive |

### Quelli che lanci

| File | Cos'è |
|---|---|
| `run_bess.py` | Il lanciatore. È l'unico comando che digiti: crea il template oppure esegue il calcolo |
| `test_bess_model.py` | I controlli di correttezza. Si lancia una volta dopo l'installazione, e ogni volta che si tocca il codice |

### Quelli che non apri mai (il motore)

| File | Cos'è |
|---|---|
| `bess_model.py` | Il cuore del calcolo: caratteristiche del BESS, tabelle di capacità e RTE, conversione della matrice prezzi, ottimizzazione giorno per giorno, calcolo dei 20 anni |
| `excel_io.py` | Tutto ciò che riguarda Excel: crea il template di input, legge i parametri e i prezzi, scrive il report di output con i grafici |

### La documentazione

| File | Cos'è |
|---|---|
| `Manuale_utente.md` | Questo documento: come si usa, in pratica, incluso come è fatto il codice |
| `README.md` | Riassunto di mezza pagina, in inglese, per chi ha fretta |
| `Riepilogo_modello_BESS.md` | Assunzioni, default, scelte di modellazione, perimetro e storico delle versioni. In inglese. È il documento da leggere prima di discutere un numero con qualcuno, ed è anche il posto dove sono finiti i parametri che nella v3 stavano nel foglio Excel `Ipotesi` |

I quattro file `.py` devono stare **nella stessa cartella** perché il modello funzioni.
I file Excel possono stare dove vuoi, basta indicare il percorso nel comando.

---

## 3. Installazione (una volta sola)

Serve Python 3.10 o successivo. Poi, da terminale:

```bash
pip install numpy pandas scipy openpyxl
```

Verifica che sia tutto a posto:

```bash
python test_bess_model.py
```

Deve rispondere `20/20 tests passed` (i messaggi dei test sono in inglese, come tutto il
codice — vedi §5.0 sul perché). Se qualche test fallisce, il modello non è
affidabile: non usarlo finché non è risolto.

---

## 4. Il ciclo di lavoro, in tre passi

```bash
# 1. crea il file di input (una volta; contiene già 20 anni di prezzi dimostrativi)
python run_bess.py --template input_bess.xlsx

# 2. apri input_bess.xlsx, compila 'Parameters' e 'Prices', salva

# 3. lancia il calcolo
python run_bess.py input_bess.xlsx risultati_bess.xlsx
```

Il passo 3 si ripete tutte le volte che vuoi: ogni lancio rilegge l'input e riscrive
l'output da zero. Se vuoi conservare uno scenario, cambia il nome del file di output
(`python run_bess.py input_bess.xlsx scenario_8h_conservativo.xlsx`).

> **I prezzi del template sono finti.** Sono una curva sintetica costruita per far vedere
> che il modello gira. Vanno sostituiti con i tuoi prima di guardare qualsiasi numero.
> Se preferisci partire da un foglio vuoto:
> `python run_bess.py --template input_bess.xlsx --no-prices`

Altre opzioni del template:

```bash
python run_bess.py --template input.xlsx --years 5            # prezzi demo solo in Year 1-5
python run_bess.py --template input.xlsx --no-prices          # foglio Prices vuoto
```

---

## 5. Compilare il file di input

Il file ha tre fogli: `Instructions` (da leggere, in inglese — vedi §5.0), `Parameters` (da
compilare, include in fondo le due tabelle obbligatorie di degrado capacità e RTE) e `Prices` (da compilare).
Non c'è più un foglio `Degrado` separato né un parametro per un file prezzi esterno: è tutto
dentro questi tre fogli.

### 5.0 Perché i fogli sono in inglese

Su richiesta esplicita, questo manuale è l'unico documento rimasto in italiano. Il codice
(messaggi di errore, testi di caricamento) e il file Excel — nomi dei fogli, etichette,
titoli dei grafici — sono tutti in inglese. Le tabelle che seguono traducono ogni etichetta
inglese che trovi nel file, così puoi usare questo manuale come dizionario mentre compili.

### 5.1 Foglio `Parameters`

Si toccano **solo le celle gialle** della colonna *Value*. Tutto il resto è etichetta.
Le celle lasciate vuote dove è previsto (potenze di carica/scarica, POI) vengono interpretate
come "non vincolante", non come zero.

**SIZING (Dimensionamento)**

| Etichetta nel file | Come sceglierlo |
|---|---|
| Nominal power | Lascia **1 MW** se vuoi ragionare per MW installato: il ricavo esce già in k€/MW e non devi dividere niente. Metti la potenza vera solo se ti servono gli euro assoluti di quel progetto specifico |
| Duration (discharge hours) | Le ore a potenza piena che la batteria riesce a erogare: 2, 4, 6, 8. L'energia installata è potenza × durata ed è il tetto del SOC |
| Max charging power / Max discharging power | Da compilare solo se sono diverse dalla nominale (capita quando l'inverter è dimensionato in modo asimmetrico). Vuote = pari alla nominale |
| Power at the grid connection point (POI) | Da compilare solo se la connessione è più stretta dell'inverter. Vuota = non vincolante |

**OPERATING WINDOW (Finestra operativa)**

| Etichetta nel file | Come sceglierlo |
|---|---|
| Minimum SOC / Maximum SOC | 5% e 95% sono i valori di prassi: nessun impianto lavora davvero da 0 a 100. Se il fornitore garantisce una finestra diversa, usa la sua |
| Starting SOC | 95% (piena) di default: la batteria arriva carica. Vale solo il primo giorno, poi il livello prosegue da un giorno all'altro. Se vuoi essere prudente e partire vuoto, metti 5% |

**CICLAGGIO (non più nel file)**

Dalla v6 i limiti sono fissi: **365 cicli equivalenti/anno** (gestito come budget annuale, non uno al giorno) e **2 cicli/giorno** (cap tecnico). Sono le costanti `MAX_CYCLES_PER_YEAR` e `MAX_CYCLES_PER_DAY` in cima a `bess_model.py`: se serve cambiarle si tocca il codice, di proposito.

**LE DUE TABELLE OBBLIGATORIE IN FONDO AL FOGLIO**

Subito sotto l'elenco dei parametri ci sono due tabelle, ciascuna con una riga per ognuno
dei 20 anni (anno 1 = 2029). Sono **obbligatorie**: arrivano precompilate, ma non puoi
cancellarle. Non serve che i valori scendano in modo regolare: scrivi esattamente i numeri
del fornitore, anno per anno.

- **CAPACITY DEGRADATION — `Capacity (%)`**: la capacità di energia dell'anno in % di quella
  di targa. Parte da **100%** e scende (es. 100%, 95%, 92%, …). Riduce i MWh che la batteria
  può spostare quell'anno. Precompilata al 100% = nessun degrado.
- **RTE — `RTE (%)`**: l'**RTE vera dell'asset** in quell'anno, non un degrado. Parte dal
  valore del primo anno e scende (es. 87%, 86%, 86%, …). Precompilata all'87% su tutti gli
  anni. Va messa l'RTE di **sistema**, AC-AC, non quella di cella (93-95%), che sovrastima il
  ricavo. Non esiste più un parametro "RTE anno 1" separato: il primo anno è la prima riga.

La **potenza non degrada**: ogni anno è quella di targa (o il limite POI, se più stretto).

Cosa sparisce rispetto alle versioni precedenti: la colonna `Power (%)` e l'RTE espressa come
percentuale dell'anno 1. Non c'è nemmeno l'augmentation (gli anni in cui si "ricaricava" la
capacità al 100% aggiungendo celle): per questo progetto le batterie non verranno mai
sostituite o ampliate.

**LOSSES AND AVAILABILITY (Perdite e disponibilità)**

| Etichetta nel file | Come sceglierlo |
|---|---|
| Auxiliary consumption (HVAC, etc.) | 0,5% della potenza, consumo continuo di HVAC e servizi, pagato al prezzo medio di mercato |
| Availability | 98%. Manutenzione programmata più guasti. Se hai un contratto di O&M con disponibilità garantita, usa quella |

**VARIABLE MARKET COSTS (Costi variabili di mercato)**

| Etichetta nel file | Come sceglierlo |
|---|---|
| Charges on withdrawal (charging) | €/MWh pagati in più quando si compra. Entrano nella decisione, non solo nel risultato: con oneri alti il BESS cicla meno, ed è giusto così |

Dalla v7 non c'è più l'onere sull'immissione (scarica).

Se non sai quali oneri si applicano al tuo caso, lasciali a 0 e ricordati che il risultato è
al lordo di quelle voci.

**OPERATIONAL REALISM (Realismo operativo)**

| Etichetta nel file | Come sceglierlo |
|---|---|
| Foresight factor | 85%. L'ottimizzatore conosce i prezzi in anticipo, un trader vero no: questo è lo sconto che paga quella differenza. Range sensato 80-90%. È il parametro più soggettivo del modello e il primo da muovere in una sensitivity |

Non c'è più, in questa sezione, un parametro per l'orizzonte di ottimizzazione: è fissato a 2
giorni dentro al codice e non compare più nel file. Il perché è spiegato nel foglio
`Instructions` del file stesso e in `Riepilogo_modello_BESS.md` §7: serve solo a evitare una
decisione sbagliata proprio a mezzanotte (svuotare la batteria alle 23:00 quando la mattina
dopo alle 3 c'è un'occasione di acquisto molto migliore), e non c'era nessuna ragione per cui
un utente non tecnico dovesse poterlo cambiare.

**SCENARIO**

| Etichetta nel file | Come sceglierlo |
|---|---|
| Sample day for the chart | La data del profilo orario di esempio nel foglio `Sample_Day`, formato AAAA-MM-GG, tra il 2029-01-01 e il 2048-12-31 |

Dalla v7 non ci sono più l'anno di partenza (è sempre il 2029), la vita utile (sempre 20
anni) e il tasso di sconto (nessuna attualizzazione). Non ci sono più nemmeno un parametro
per un file prezzi esterno o per l'escalation dei prezzi. Il primo è sparito perché il foglio `Prices` è ora l'unico modo di
dare i prezzi al modello; il secondo perché, se vuoi che i prezzi crescano nel tempo, lo
scrivi tu direttamente nelle colonne-anno del foglio `Prices` (vedi §5.2) — il modello non fa
più ipotesi automatiche di crescita al posto tuo.

### 5.2 Foglio `Prices`

Struttura fissa:

| day | hour | Year 1 (2029) | Year 2 (2030) | … | Year 20 (2048) |
|---|---|---|---|---|---|
| 1 | 1 | 62,04 | 74,61 | | |
| 1 | 2 | 38,72 | 50,88 | | |
| … | … | | | | |
| 365 | 24 | | | | |

8.760 righe (365 giorni × 24 ore), una colonna per anno operativo. Le colonne `day` e `hour`
sono già compilate e servono solo a orientarti: non toccarle.

**Quali prezzi mettere.** I prezzi orari **zonali** del mercato del giorno prima della zona
in cui sta il progetto: NORD, CNOR, CSUD, SUD, SICI, SARD. Non il PUN, che è una media
nazionale pesata e non esiste come prezzo a cui si compra o si vende. Usare la zona
sbagliata è l'errore più costoso che si possa fare in questo foglio, perché il ricavo di un
BESS dipende tutto dallo spread infragiornaliero locale.

**Quante colonne compilare.** La `Year 1` (2029) è obbligatoria; le altre puoi compilarle
tutte o in parte. Il modello gira sempre 20 anni e la regola per quelle vuote è una sola:
**ogni anno lasciato vuoto prende i prezzi dell'ultimo anno compilato prima di lui**.

- Tutte e 20 compilate (consigliato): ogni colonna è il suo anno, usata così com'è.
- Solo la `Year 1`: il 2029 viene ripetuto identico per 20 anni.
- `Year 1`-`Year 5`: dal 2034 al 2048 si ripete la `Year 5`.
- Una colonna vuota in mezzo (es. `Year 3`): prende i prezzi della `Year 2`, e le colonne
  dopo restano al loro anno (non "scalano").

Ripetere un anno vuol dire assumere che lo spread smetta di evolvere da lì in poi: va bene per
un primo conto, ma se vuoi una crescita devi scriverla tu nelle colonne. Mentre gira, il
modello stampa quali anni sono stati ripetuti e da quale colonna.

**Il calendario è "tipo".** 365 giorni da 24 ore esatte: niente anni bisestili, niente ora
legale. Se parti da una serie storica reale devi quindi buttare via il 29 febbraio degli
anni bisestili e sistemare i due giorni del cambio d'ora (quello da 23 ore va completato,
quello da 25 va tagliato) prima di incollare. È una semplificazione voluta: su un profilo
prospettico a 20 anni, un'ora in più o in meno non sposta nulla, e in cambio ogni giorno ha
lo stesso numero di righe.

Non esiste più l'opzione di un file prezzi esterno in formato CSV: qualunque cosa tu abbia,
va trasformata in questa matrice prima di essere incollata nel foglio.

### 5.3 Checklist prima di lanciare

- I prezzi dimostrativi sono stati sostituiti con quelli veri, della zona giusta
- Le colonne-anno compilate non hanno buchi: se una colonna la usi, va riempita tutta
- Le due tabelle in fondo a `Parameters` riflettono le curve vere: capacità in % (da 100% a scendere) e RTE vera dell'asset (es. 87% a scendere)
- Potenza e durata corrispondono al progetto che stai valutando
- La colonna `Year 1` contiene i prezzi del 2029: l'anno 1 è sempre il 2029
- Il giorno campione cade dentro il periodo dei prezzi
- Il file è **salvato e chiuso** (Excel tiene il lock sul file aperto)

---

## 6. Lanciare il calcolo

```bash
python run_bess.py input_bess.xlsx risultati_bess.xlsx
```

Mentre gira stampa un riepilogo di controllo, in inglese — conviene leggerlo, perché è lì che
ci si accorge di aver sbagliato input:

```
  BESS: 1 MW / 8 h (8 MWh), RTE 87.0% (year 1) -> 87.0% (year 20), capacity 100% in year 20
  Prices: 175,200 observations, 2029-2048, average 81.9 EUR/MWh, 336 negative hours
  year  1/20 (2029):   118.9 k EUR/MW  (366 cycles, 365 days, capacity 100.0%, prices of Year 1)
  ...
```

Se hai lasciato vuote alcune colonne stampa anche una riga del tipo
`5 year-column(s) filled; empty years repeat the last filled one: 2034<-Year 5, ...`, che
dice quale anno ha preso i prezzi da quale colonna.

Se il prezzo medio o gli anni non sono quelli che ti aspetti, ferma tutto e ricontrolla il
foglio `Prices`. Venti anni di dati orari richiedono circa un minuto, un po' di più se il
foglio `Raw_Data` dell'output deve scrivere tutte le 175.200 righe.

---

## 7. Leggere il file di output

Quattro fogli, in quest'ordine — tre in meno rispetto alla v3, perché `Risultati_Annuali` e
`Dati_Puliti` sono stati uniti, `Dettaglio_Anno1` è diventato il molto più completo
`Raw_Data`, e `Ipotesi` è stato tolto (il suo contenuto vive ora in
`Riepilogo_modello_BESS.md`).

**`Summary`** — i KPI che servono per parlarne con qualcuno: ricavo del primo anno, ricavo
medio annuo (in k€/MW e in k€/MWh installato), cicli medi,
spread medio catturato, prezzi medi di acquisto e vendita, capacità residua a fine vita.
Più quattro grafici: ricavo per anno, capacità e RTE per anno, cicli per anno, e acquisti/vendite
per anno (quest'ultimo è nuovo in v4: prima si vedeva solo in `Dati_Puliti`). Le linee dei
grafici sono state rese sottili apposta, per non coprire titoli ed etichette con 20 anni di
dati.

**`Annual_Results`** — la tabella completa, una riga per anno operativo, con tutte le colonne
già calcolate: MWh comprati e venduti, euro spesi e incassati sul mercato (al netto di
fattore di realizzazione e disponibilità), costo ausiliari, ricavo netto in k€, in k€/MW e in
k€/MWh, SOC medio, cicli, capacità (% e MWh) e RTE dell'anno, prezzi medi, spread
catturato. È la fonte dei grafici del foglio `Summary` e il foglio da cui ripartire se vuoi
rifare i conti per conto tuo.

> **Nota per chi conosceva la v3.** Questo foglio unisce quello che prima erano
> `Risultati_Annuali` (i valori grezzi che uscivano dal motore) e `Dati_Puliti` (la
> rielaborazione fatta con formule Excel dal vivo). In v4 tutta la tabella è calcolata da
> python e scritta come valori: cambiare il fattore di realizzazione o la disponibilità
> **dentro questo foglio** non ricalcola più nulla in automatico. È una scelta deliberata —
> tenere un secondo foglio che esisteva solo per "ombreggiare" il primo con formule era
> proprio il tipo di complessità che questa versione voleva togliere. Per cambiare quei due
> parametri, cambiali nel foglio `Parameters` del file di input e rilancia: ci vuole un
> minuto.

**`Raw_Data`** — il diretto output orario del modello, per **ogni ora di ogni anno**: anno
operativo, anno solare, giorno (1-365), ora (1-24), SOC in MWh, energia comprata in MWh,
energia venduta in MWh. Con 20 anni sono 175.200 righe. È il dato più "grezzo" che il modello
produce: nessuna rielaborazione, nessun arrotondamento oltre alla precisione numerica. Serve
per chi vuole verificare il comportamento ora per ora, costruire grafici propri, o controllare
che la batteria non stia facendo qualcosa di strano in un giorno preciso.

> **Nota per chi conosceva la v3.** Questo foglio sostituisce `Dettaglio_Anno1`, che copriva
> solo il primo anno con un dettaglio giornaliero (non orario). Su richiesta esplicita, ora
> il dettaglio è orario e copre tutti gli anni, non solo il primo.

**`Sample_Day`** — il profilo orario di una giornata: prezzo, carica, scarica, SOC, con il
grafico. Serve a vedere *come* ragiona l'ottimizzatore, ed è il modo più rapido per accorgersi
se qualcosa non torna (per esempio una batteria che non scarica mai la sera).

### Come si leggono i numeri chiave

**k€/MW e k€/MWh.** Il ricavo per MW è sempre diviso per la potenza **di targa**, mai per
quella degradata dell'anno: è quello che permette di vedere il calo dovuto all'invecchiamento
guardando la serie degli anni. Per tornare agli euro: `€ = k€/MW × 1.000 × potenza (MW)`.
Il k€/MWh installato è la metrica giusta per confrontare durate diverse (una 8h incassa più
euro di una 4h, ma meno euro per MWh installato).

**Cicli equivalenti.** Possono risultare 366-368 con un budget di 365: la ricerca del
prezzo-ombra si ferma entro l'1% del budget, uno scarto voluto per non moltiplicare i tempi
di calcolo. Se ti serve il rispetto stretto del limite, si abbassa la costante
`MAX_CYCLES_PER_YEAR` nel codice (per esempio a 360).

**Prezzo-ombra del ciclo (colonna "Cycle shadow price").** È quanto il modello ha dovuto "far
costare" un ciclo per stare dentro al budget annuale. Letto al contrario, è una misura di
quanto il vincolo dei 365 cicli sta stringendo: vicino a zero significa che la batteria cicla
poco perché non conviene, alto significa che ci sono molte più occasioni redditizie di quante
il budget ne conceda.

**Spread catturato.** Differenza tra prezzo medio di vendita e prezzo medio di acquisto. Non
è lo spread del mercato: è quello che la batteria è davvero riuscita a prendersi, al netto
dei limiti di potenza, capacità e cicli.

**Il primo anno rende un po' di più.** La batteria parte carica, quindi il primo giorno
vende energia che non ha comprato. È un guadagno una tantum di al massimo una scarica
completa: su vent'anni è irrilevante, ma se guardi solo l'anno 1 sappi che c'è.

---

## 8. Errori e cosa significano

I messaggi che il modello stampa sono in inglese; qui la traduzione e cosa fare.

| Messaggio (inglese) | Cosa è successo | Cosa fare |
|---|---|---|
| `The file must contain the 'Parameters' and 'Prices' sheets` | Il file di input non è quello giusto, o i fogli sono stati rinominati | Rigenera il template e ricompila |
| `Could not find the 'Year 1' column in the 'Prices' sheet` | Le intestazioni della prima riga sono state cancellate o rinominate | Rimetti `Year 1`, `Year 2`, … nella riga 1 |
| `'Year N' in the 'Prices' sheet has N empty or non-numeric cells` | Una colonna-anno è riempita solo in parte, o contiene testo | Completala, o svuotala del tutto se quell'anno non ti serve |
| `'Year 1' (2029) is empty in the Prices sheet: it is mandatory` | La colonna del 2029 è vuota | Compila la `Year 1` |
| `Could not find the ... table in the Parameters sheet` / `The ... table is missing the value for year N` | Una delle due tabelle annuali è stata spostata, cancellata o lasciata con un buco | Rigenera il template, oppure completa la riga mancante |
| `The ... table has X for year N: write it as a percentage` | Un valore delle tabelle non è tra 0% e 100% (es. scritto 87 invece di 87%) | Scrivilo come percentuale |
| `Found N duplicate timestamps` / `N missing (NaN) values` | Problema nella serie prezzi (più raro nel foglio matrice, tipico se si manipola la serie a mano in python) | Controlla che non ci siano righe duplicate o celle vuote |
| `Days with an abnormal number of observations` | Buchi nella serie: qualche giorno non ha 24 valori | Cerca i giorni segnalati nel messaggio e completali |
| `Permission denied` sul file di output | Il file di output è aperto in Excel | Chiudilo e rilancia |
| `Optimization failed` | Vincoli incompatibili (es. SOC minimo maggiore del massimo) | Ricontrolla la finestra operativa |
| `(sample day not found in the price series: chart skipped)` | Non è un errore: salta solo quel grafico | Metti una data dentro il periodo dei prezzi |

Rispetto alla v3 sono scomparsi i messaggi legati al file prezzi esterno (`File prezzi
esterno non trovato` e simili): non esistendo più quell'opzione, non possono più comparire.

---

## 9. Cosa il modello **non** calcola

Il risultato è un ricavo lordo di mercato da **solo arbitraggio** sul mercato del giorno
prima. Restano fuori, per scelta esplicita e non per dimenticanza:

- **Qualsiasi altro ricavo** oltre all'arbitraggio sul giorno prima (meccanismi di capacità
  compresi): va aggiunto a valle, nel business plan
- **MSD e servizi ancillari**: fuori perimetro. Un tentativo in v5 (MSD come ottimizzazione
  indipendente da MGP) è stato scartato: usano la stessa batteria, quindi non sono indipendenti
  e trattarli come tali gonfia il ricavo; accoppiarli bene è overkill per questo modello
- **Augmentation** (sostituzione o aggiunta di celle a metà vita): tolta in v4 perché le
  batterie di questo progetto non verranno mai cambiate
- **CAPEX, OPEX, fiscalità, costi di sbilanciamento**
- **Attualizzazione**: tolta in v7. Il modello dà il ricavo di ogni anno; lo sconto lo fa il
  business plan

In pratica: il numero che esce da qui è la riga "Arbitrage" del business plan. Tutto il resto vive nel file di business plan.

---

## 10. Come funziona il codice, spiegato a blocchi

Questa sezione esiste per una ragione precisa: non devi fidarti alla cieca del modello.
Anche senza saper programmare, puoi seguire cosa fa ogni pezzo di codice leggendo questa
sezione insieme al file. Non serve leggerla tutta in una volta: usala come riferimento
quando ti chiedi "questo numero da dove esce?".

Ci sono due file di codice "motore": `bess_model.py` (i calcoli) e `excel_io.py` (la lettura
e scrittura dei file Excel). `run_bess.py` è solo il collegamento tra i due e il terminale.

### 10.1 `bess_model.py` — il motore di calcolo

**Le costanti in cima al file.** Prima di ogni funzione ci sono alcuni valori
fissi:

- `TZ = "Europe/Rome"`: il fuso orario italiano vero, usato quando i prezzi arrivano come
  serie storica reale (per esempio se in futuro si volesse ricollegare a dati GME).
- `TZ_TYPICAL_YEAR`: un fuso "finto", fisso a +1 ora tutto l'anno. Serve a rappresentare il
  "calendario tipo" del foglio `Prices` (365 giorni da 24 ore esatte) senza che il codice
  Python provi a inserire l'ora legale o il 29 febbraio, cosa che romperebbe la struttura a
  matrice.
- `HORIZON_DAYS = 2`: l'orizzonte di ottimizzazione, spiegato in §5.1 e in
  `Riepilogo_modello_BESS.md` §7. È scritto qui, non in un parametro Excel, proprio perché
  non deve essere modificabile dall'utente.
- `MAX_CYCLES_PER_YEAR = 365` e `MAX_CYCLES_PER_DAY = 2`: i limiti di ciclaggio (v6).
- `START_YEAR = 2029` e `LIFETIME_YEARS = 20`: l'anno 1 è sempre il 2029 e gli anni sono
  sempre 20 (v7).

**Blocco 1 — `class BessSpec`: la "scheda tecnica" della batteria.** È il
contenitore di tutti i parametri tecnici: potenza, durata, finestra di SOC, le due tabelle
annuali (capacità e RTE), onere sul prelievo, fattore di realizzazione. Ogni parametro del foglio
`Parameters` finisce in un campo di questa struttura (la mappa completa è in §11). Da notare
due proprietà calcolate automaticamente, non scritte da te:

- `energy_mwh`: potenza × durata. È il tetto del SOC.
- `year_factors(anno)`: la funzione che, dato un numero di anno (1, 2, 3, …), restituisce le
  caratteristiche di quell'anno: l'energia (energia di targa × percentuale della tabella di
  capacità), l'RTE (letta così com'è dalla tabella RTE, perché è già l'RTE vera) e la potenza
  (sempre quella di targa). Se l'anno richiesto è oltre l'ultima riga di una tabella, usa
  l'ultima riga disponibile invece di dare errore.

**Blocco 2 — preparazione dei prezzi.** Tre funzioni che puliscono e
verificano la serie di prezzi prima di ogni calcolo:

- `is_typical_calendar`: riconosce se una serie è un "calendario tipo" (fuso fisso, vedi
  sopra) oppure una serie reale. Serve alle altre funzioni per decidere se convertire il fuso
  orario o lasciare tutto com'è.
- `prepare_price_series`: converte la serie sul fuso italiano, **a meno che** non sia già un
  calendario tipo, nel qual caso la lascia intoccata (convertirla sposterebbe le ore senza
  motivo).
- `validate_price_series`: controlli di qualità — niente orari duplicati, niente valori
  mancanti, nessun giorno con un numero di osservazioni anomalo (tollerando i giorni di 23/25
  ore dell'ora legale su serie reali, non su calendari tipo che non li hanno mai).

**Blocco 2-bis — `fill_missing_years` e `matrix_to_series`: dalla matrice del foglio Excel
a una serie temporale.** `fill_missing_years` (v7) prende le 20 colonne-anno lette dal foglio
`Prices` e riempie quelle vuote con l'ultimo anno compilato prima di loro; restituisce anche,
per ogni anno, da quale colonna arrivano i prezzi. `matrix_to_series` prende poi la matrice
completa (8.760 righe × 20 colonne) e la trasforma in un'unica serie continua: la prima
colonna diventa il 2029, la seconda il 2030, e così via, incollate una dietro
l'altra. Da qui in poi il resto del codice tratta tutto come se fosse sempre stata una serie
storica lunga — è il punto in cui il formato "comodo da compilare a mano" (matrice) diventa il
formato "comodo da calcolare" (serie).

**Blocco 3 — `optimize_window`: il programma lineare vero e proprio.** È il
cuore matematico del modello. Riceve un pezzo di curva prezzi (una finestra di `HORIZON_DAYS`
giorni) e restituisce, ora per ora, quanto caricare e quanto scaricare per massimizzare il
margine. Le variabili sono `carica` e `scarica` (in MW, sempre ≥ 0: non si può caricare o
scaricare "in negativo"); i vincoli sono: potenza massima di carica/scarica, finestra di SOC
(il livello di carica non può mai uscire da min/max), e il budget di cicli passato come
parametro. **Prezzi negativi (v7):** nelle ore con prezzo negativo il tetto della variabile
`scarica` è zero invece della potenza massima, quindi in quelle ore la batteria può solo
caricare o stare ferma. È un limite sulla variabile, non un vincolo a numeri interi: il
problema resta lineare. Un dettaglio tecnico che vale la pena capire: il "prezzo del ciclo"
(`cycle_price`) viene sommato dentro alla funzione che il solutore cerca di ottimizzare, non
imposto come un vincolo rigido — è così che il modello decide *da solo* quando conviene
consumare un ciclo e quando no, invece di seguire una regola fissa.

**Blocco 4 — la simulazione di un anno.** Tre funzioni che lavorano insieme:

- `_run_year_at_lambda`: fa scorrere `optimize_window` giorno per giorno su un intero anno,
  con un dato "prezzo del ciclo" (lambda). Per ogni giorno guarda oggi + domani (i
  `HORIZON_DAYS` giorni), ma tiene solo la decisione di oggi, poi passa il SOC finale di oggi
  come SOC iniziale di domani: è qui che il SOC "continua" da un giorno all'altro invece di
  azzerarsi ogni notte. Da qui escono anche i tre array orari — SOC, energia comprata, energia
  venduta — che finiranno, per tutti gli anni, nel foglio `Raw_Data`.
- `simulate_year`: cerca, per bisezione, il valore di lambda che fa rispettare il budget
  annuale di cicli (365) entro l'1% di tolleranza. Prova prima
  l'ultimo lambda trovato per l'anno precedente (di solito è già quasi giusto, perché il
  degrado cambia poco da un anno all'altro), poi allarga o restringe la ricerca finché il
  totale dei cicli dell'anno non è abbastanza vicino al budget.
- `_assemble_year`: prende il risultato migliore trovato e calcola tutti i numeri che vedi
  poi nel foglio `Annual_Results` — ricavo grezzo, ricavo netto, MWh comprati/venduti, ore
  equivalenti, prezzo medio di acquisto/vendita, e così via.

**Blocco 5 — `simulate_lifetime`: il calcolo dei 20 anni.** Ogni anno solare della serie
(2029-2048) è un anno operativo. Per ogni anno chiama `simulate_year`, passando come punto di
partenza il lambda trovato l'anno prima (più veloce), e accumula il dettaglio orario di tutti
gli anni in un'unica tabella (`hourly_all`), che è esattamente quello che finisce nel foglio
`Raw_Data`. Dalla v7 non applica nessuno sconto finanziario: il risultato è il ricavo di ogni
singolo anno.

**Blocco 6 — generatori di dati sintetici.** `make_synthetic_prices` e
`make_synthetic_matrix` creano curve di prezzo finte, plausibili nella forma (picco serale,
minimo notturno, stagionalità), usate **solo** per riempire il template di esempio e per i
test. Non vanno mai usate per un numero vero. `make_flat_capacity_table` e `make_flat_rte_table`
generano le due tabelle di default (capacità 100% e RTE 87% ovunque) con cui il template arriva
precompilato.

### 10.2 `excel_io.py` — tutto ciò che tocca Excel

Se un giorno vuoi cambiare un'etichetta, un colore, o aggiungere una colonna all'output, è
quasi sempre questo il file da aprire, non `bess_model.py`.

**`PARAMS`: la lista che genera il foglio `Parameters`.** Ogni riga della lista
è una riga del foglio: una chiave interna (usata dal codice), l'etichetta che vedi in Excel,
il valore di default, l'unità di misura, il formato numerico e una nota. Le righe con chiave
`"__SEC__"` non sono parametri: disegnano le intestazioni di sezione grigie (SIZING,
OPERATING WINDOW, …). Aggiungere o togliere un parametro dal file si fa aggiungendo o
togliendo una riga qui (vedi §12).

**`_write_year_tables` e `_read_year_table`: le due tabelle annuali.** La prima scrive sotto
l'elenco dei parametri le due tabelle (degrado capacità e RTE), già precompilate, quando crei
il template. La seconda le rilegge quando lanci il calcolo: trova ciascuna tabella cercando
la riga di intestazione (`Year` | `Capacity (%)` oppure `Year` | `RTE (%)`) e legge un valore
per anno. Se manca un valore, o se un valore non è una percentuale tra 0 e 100%, dà un errore
chiaro invece di proseguire con un dato sbagliato.

**`_read_price_matrix` (funzione che legge il foglio `Prices`).** Legge la riga di intestazione
e mette ogni colonna al suo anno in base al numero scritto nell'intestazione (`Year N`), così
una colonna vuota non fa "scalare" le altre. Controlla che le colonne compilate non abbiano
buchi a metà, poi passa tutto a `fill_missing_years`, che riempie gli anni vuoti con l'ultimo
anno compilato prima di loro.

**`read_input` (funzione che legge tutto il file di input).** Il punto di ingresso usato da
`run_bess.py`: apre il file, legge i parametri uno per uno dal foglio `Parameters`
(comprese le due tabelle annuali), legge la matrice prezzi dal foglio `Prices`, e restituisce
tre cose: la scheda tecnica (`BessSpec`), la serie di prezzi già convertita, e un dizionario
con il giorno campione e, per ogni anno, da quale colonna arrivano i prezzi.

**`ANNUAL_COLS` e `RAW_COLS`: le colonne dei due fogli principali dell'output.** Come
`PARAMS`, sono liste che accoppiano una chiave interna a un'etichetta e un formato numerico.
`ANNUAL_COLS` governa il foglio `Annual_Results` (una riga per anno), `RAW_COLS` governa
`Raw_Data` (una riga per ogni ora di ogni anno). Cambiare cosa compare in questi due fogli si
fa aggiungendo o togliendo una riga da queste liste (vedi §12).

**`write_output` (la funzione più lunga del file): scrive il file `risultati_bess.xlsx`.**
Crea i quattro fogli nell'ordine `Summary`, `Annual_Results`, `Raw_Data`, `Sample_Day`,
calcola le poche colonne derivate che non arrivano già pronte dal motore (es. lo spread,
i k€ comprati/venduti), scrive i KPI e i quattro grafici del foglio `Summary`, e infine
scrive `Raw_Data` senza applicare stili cella per cella (`_write_raw_data`): con 175.200
righe, colorare ogni cella singolarmente renderebbe il file enorme e lentissimo da scrivere,
quindi lì si formatta solo l'intestazione.

**`_thin_line` e `_style_chart`: l'aspetto dei grafici.** Due funzioni piccole ma dedicate
proprio alla richiesta di grafici più leggibili: `_thin_line` disegna ogni linea con uno
spessore fisso (2,25pt, perché i colori pastello restino visibili) e senza marcatori per
punto, così 20 categorie annuali non diventano un blocco pieno che copre titoli ed etichette;
`_style_chart` uniforma dimensioni e posizione della legenda su tutti i grafici.

---

## 11. Da dove viene ogni parametro: la mappa parametro → codice

Questa tabella collega ogni riga del foglio `Parameters` (e le due tabelle annuali) al punto
esatto del codice dove entra in gioco. Utile se un numero in output ti sembra strano e vuoi
capire quale parametro lo sta determinando.

| Etichetta in Excel | Campo/uso nel codice | Dove entra nel calcolo |
|---|---|---|
| Nominal power | `BessSpec.power_mw` | Base di `energy_mwh` (= potenza × durata) e denominatore di tutti i k€/MW in output |
| Duration (discharge hours) | `BessSpec.duration_h` | Come sopra: `energy_mwh = power_mw * duration_h`, è il tetto del SOC in `optimize_window` |
| Max charging power / Max discharging power | `BessSpec.power_charge_mw` / `power_discharge_mw` (proprietà `p_charge`/`p_discharge`) | Limite superiore della variabile "carica"/"scarica" in `optimize_window` |
| Power at the grid connection point (POI) | `BessSpec.poi_limit_mw` | Applicato dentro `p_charge`/`p_discharge` come tetto ulteriore, se più stringente |
| Minimum SOC / Maximum SOC | `BessSpec.soc_min_frac` / `soc_max_frac` | Diventano `soc_min`/`soc_max` (in MWh) in `_run_year_at_lambda`, vincolo di `optimize_window` |
| Starting SOC | `BessSpec.soc_start_frac` | SOC di partenza del primo giorno dell'anno in `_run_year_at_lambda` (poi il SOC prosegue da solo) |
| Tabella CAPACITY DEGRADATION (Year, Capacity %) | `BessSpec.capacity_table`, letta da `year_factors` | Energia dell'anno = energia di targa × percentuale: è il tetto del SOC in `optimize_window` per quell'anno |
| Tabella RTE (Year, RTE %) | `BessSpec.rte_table`, letta da `year_factors` | È l'RTE vera dell'anno, usata così com'è; entra in `optimize_window` come `eta_c`/`eta_d` (radice quadrata dell'RTE, perché l'RTE si applica una volta in carica e una in scarica) |
| Auxiliary consumption (HVAC, etc.) | `BessSpec.aux_load_pct_of_power` | Moltiplicato per potenza e ore in `_assemble_year` per ottenere `aux_mwh`, sottratto dal ricavo lordo |
| Availability | `BessSpec.availability_pct` | Fattore moltiplicativo (insieme al foresight factor) applicato al ricavo lordo in `_assemble_year` |
| Charges on withdrawal (charging) | `BessSpec.charge_cost_eur_mwh` | Sommato al prezzo nella funzione obiettivo di `optimize_window`: cambia le decisioni di carica, non solo il conto finale |
| Foresight factor | `BessSpec.foresight_factor` | Fattore moltiplicativo sul ricavo lordo in `_assemble_year`, insieme alla disponibilità |
| Sample day for the chart | `scenario["example_day"]`, passato a `example_day_profile` | Seleziona il giorno mostrato nel foglio `Sample_Day` |

Non compaiono in questa tabella i limiti di ciclaggio (`MAX_CYCLES_PER_YEAR` = 365 e `MAX_CYCLES_PER_DAY` = 2: costanti in cima a `bess_model.py`, usate come `budget` in `simulate_year` e come vincolo `max_cycles` in `optimize_window`) né l'orizzonte di ottimizzazione: come spiegato in §5.1 e §10.1,
è la costante `HORIZON_DAYS = 2` scritta in cima a `bess_model.py`, e non un parametro che
arriva dal file Excel. Lo stesso vale, dalla v7, per l'anno di partenza (`START_YEAR = 2029`,
usato da `matrix_to_series`) e per il numero di anni (`LIFETIME_YEARS = 20`, usato da
`fill_missing_years`).

---

## 12. Se serve mettere mano al codice

Quattro punti di riferimento, in ordine di frequenza con cui si toccano:

- **Aggiungere o cambiare un parametro di input**: si aggiunge una riga alla lista `PARAMS`
  in `excel_io.py` e il corrispondente campo in `BessSpec` dentro `bess_model.py`. Il
  template si aggiorna da solo al prossimo `--template`
- **Cambiare le tabelle annuali (capacità e RTE)**: `_write_year_tables` e `_read_year_table`
  in `excel_io.py` governano rispettivamente la scrittura nel template e la lettura; la
  logica che le applica anno per anno è `BessSpec.year_factors` in `bess_model.py`
- **Cambiare una colonna dell'output**: le liste `ANNUAL_COLS` e `RAW_COLS` in `excel_io.py`
  governano rispettivamente il foglio `Annual_Results` e il foglio `Raw_Data`
- **Toccare la logica di dispacciamento**: `optimize_window` in `bess_model.py` è il
  programma lineare vero e proprio; `simulate_year` gestisce il budget annuale di cicli

Dopo qualsiasi modifica, `python test_bess_model.py` deve continuare a dare 20/20. Tra i
test ce n'è uno (`test_revenue_identity`) che verifica che la formula usata per il ricavo
netto nel foglio `Annual_Results` dia esattamente lo stesso numero del motore python: se si
rompe quello, output Excel e calcolo hanno smesso di raccontare la stessa storia. Un altro
(`test_hourly_output_covers_every_year`) verifica che il dettaglio orario copra davvero tutti
gli anni e non solo il primo.
