# Phase 3B — Benchmark voix française masculine locale

Phrase identique pour tous les candidats :

> « Bonsoir Jérôme. Tous les systèmes sont opérationnels. Que puis-je faire pour vous ? »

Machine : Windows 11, mesures prises à modèle chargé (cas réel : la voix est
chargée une fois au démarrage). Reproduire avec :

```bash
.venv/bin/python bench/voice/bench_voices.py
.venv/bin/python bench/voice/bench_expressivity.py
```

## 1. Moteurs écartés, et pourquoi

Le cahier des charges impose : **français + voix masculine + gratuit + local +
licence vérifiée + aucun clonage non autorisé**. Trois moteurs sérieux ont été
examinés avant d'être écartés sur un critère factuel, pas sur une impression :

| Moteur | Licence | Verdict |
|---|---|---|
| **Kokoro-82M** | Apache-2.0 ✅ | Une seule voix française, `ff_siwis`, **féminine**. Échoue « voix masculine ». |
| **Coqui XTTS v2** | **CPML, non commercial** ❌ | Échoue « licence vérifiée ». C'est en outre un modèle de clonage. |
| **MeloTTS** | MIT ✅ | Le `config.json` français ne déclare qu'un locuteur (`{"FR": 0}`), féminin. Échoue « voix masculine », et impose une installation torch de plusieurs Go. |

Il n'existe par ailleurs **aucun palier `high`** pour le français chez
`rhasspy/piper-voices` : les qualités publiées sont `medium` (tom, upmc, mls) et
`low` (gilles, siwis, mls_1840).

## 2. Candidats mesurés

Cinq voix masculines réellement installées, mesurées sur la même phrase :

| Voix | 1er audio | Génération | RTF | Durée | SR | SNR | BW95 | Phonèmes manquants |
|---|---|---|---|---|---|---|---|---|
| **Piper — tom (medium)** | **125 ms** | **455 ms** | **11,8×** | 5,35 s | **44,1 kHz** | **43 dB** | **8358 Hz** | 0 |
| Piper — upmc / pierre | 549 ms | 1008 ms | 4,4× | 4,40 s | 22 kHz | 26,6 dB | 4008 Hz | 0 |
| Piper — mls / 1840 | 145 ms | 436 ms | 21,8× | 9,52 s | 22 kHz | 28,8 dB | 2834 Hz | 0 |
| Piper — mls / 123 | 172 ms | 548 ms | 21,6× | 11,83 s | 22 kHz | 28,0 dB | 813 Hz | 0 |
| Piper — gilles (low) | 1013 ms | 1862 ms | 5,9× | 10,96 s | 16 kHz | 50,0 dB | 1005 Hz | **1 (`̃`)** |

Lecture :

* **`gilles`** perd le tilde nasal `̃`. Les voyelles nasales sont constitutives
  du français : disqualifié sur la correction de la langue.
* **`mls`** (les deux locuteurs) produit 9,5 à 11,8 s pour une phrase qui en
  fait 5 : débit deux fois trop lent, et une bande passante de 813 à 2834 Hz.
  Sorties dégénérées, inutilisables.
* **`upmc/pierre`** concentre 93 % de l'énergie de parole sous 1 kHz, avec un
  SNR de 26,6 dB : timbre étouffé, façon téléphone.
* **`tom`** est le seul à conjuguer un débit naturel (5,35 s), une bande
  passante complète (8358 Hz), un bruit de fond bas (43 dB) et la latence la
  plus faible (125 ms au premier échantillon).

**Conclusion : `fr_FR-tom-medium` reste le meilleur choix.** Le benchmark
confirme le moteur en place au lieu de le remplacer — aucun changement de voix
n'est justifié par les mesures.

## 3. Expressivité : résultat négatif assumé

Piper expose `noise_scale` (variation de timbre) et `noise_w_scale` (variation
de durée des phonèmes), que JARVIS n'utilisait pas. Quatre profils ont été
comparés au réglage courant sur **5 synthèses chacun**, en mesurant l'étendue
de F0 (en demi-tons) et la régularité du rythme :

| Profil | Étendue F0 | Rythme σ | Écart vs courant |
|---|---|---|---|
| courant (1.0 / 0.667 / 0.8) | 7,73 ± 0,20 dt | 0,1430 ± 0,0147 | référence |
| posé | 7,63 ± 0,51 | 0,1422 ± 0,0109 | −0,30 σ / −0,07 σ |
| naturel | 7,21 ± 0,45 | 0,1445 ± 0,0201 | −1,62 σ / +0,09 σ |
| expressif | 7,37 ± 0,58 | 0,1467 ± 0,0077 | −0,94 σ / +0,33 σ |
| très expressif | 7,79 ± 0,40 | 0,1312 ± 0,0181 | +0,19 σ / −0,72 σ |

**Aucun profil ne dépasse le bruit de mesure.** Les écarts tiennent tous dans
±1 σ. Le réglage est donc exposé dans les paramètres parce que le moteur le
supporte, **pas** comme un gain de naturel démontré — et la valeur par défaut
reste le réglage courant.

## 4. Défauts réels corrigés à cette occasion

Le benchmark a révélé trois défauts qui, eux, sont mesurables :

1. **En-tête WAV invalide.** Le champ `bitsPerSample` du bloc `fmt` recevait
   `sample_width` (2 octets) au lieu de 16 bits. Tout décodeur respectant
   l'en-tête lisait un flux annoncé en 2 bits. Corrigé.
2. **Locuteurs multi-voix inatteignables.** `speaker_id` était passé en
   argument nommé de `synthesize()`, ce qui lève un `TypeError` : toute voix
   multi-locuteurs retombait silencieusement sur son locuteur 0. Il se passe
   par `SynthesisConfig`. Corrigé — `upmc/pierre` et les 125 locuteurs MLS sont
   désormais adressables.
3. **Capacité d'alignement supposée au lieu d'être sondée.** `include_alignments`
   existe dans l'API Piper, mais les modèles installés n'exportent pas les
   durées (`phoneme_id_samples` vaut `None`). La capacité est maintenant
   réellement sondée par voix, et l'appelant retombe proprement sur
   `synthesize()`. **Conséquence pour la phase 4 : aucun visème piloté par
   phonèmes n'est possible avec ces modèles** ; l'avatar doit rester sur
   l'amplitude du signal.

## 5. Repli

L'ancien chemin reste en place : `synthesize()` sans locuteur ni expressivité
se comporte exactement comme avant. Une voix absente renvoie `None` sans lever,
et l'interface bascule alors sur le moteur du navigateur.
