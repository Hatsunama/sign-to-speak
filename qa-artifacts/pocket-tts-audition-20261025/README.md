# Pocket TTS English 18M / Alba reference audition

Experimental listening samples, not an Android feature or accepted release voice.

- Exact app-repository harness/workflow source: 9763f6d00f7e4e0def4c94e68774ecb17f5a5779.
- Successful hosted CPU run: https://github.com/Hatsunama/sign-to-speak/actions/runs/40246343159 .
- GitHub artifact ID: 12618653301; ZIP SHA-256 6418403f435074ab1e1951bff722a26700d12d79b2b3e841d1726a05ecce57f9; 395,513 bytes. The downloaded ZIP matched that digest, and every WAV matched result.json. Five mono PCM16 WAVs are 24 kHz with the recorded frame counts, 10.4 seconds total.
- Synthesis ran in a separate Linux network namespace with no external networking. Models/config were provisioned first and verified by pinned hash. Torch was CPU-only; no GPU or service inference was used.
- Runtime source: Kyutai Pocket TTS 4.0.1, a922b912dfa366975294afb568d815b305acde5d. Model/voice/tokenizer: ungated no-cloning revision a7056f505a4a8ca72d8b5fb1440071d6f1a35739, plain English 18M and Alba only. See the exact asset hashes and I/O-only local-resolver adapter disclosure in result.json.
- The model load call took 0.181 seconds after Python/library imports; measured peak process RSS was 591.18 MiB. Full-utterance synthesis ranged from 0.349 to 0.789 seconds on this hosted CPU. These five samples are not percentile estimates and do not establish Android load, latency, memory, voice quality or pronunciation accuracy. No random seed was frozen, so source/asset reproducibility does not promise bit-identical resynthesis.
- Eight behavior tests passed in the hosted environment. Parent checks also passed the 27 workflow policy cases. The initial manual-only workflow could not dispatch from a non-default branch; the repaired trigger uses the exact head of the permitted same-repository draft PR. Forks and other branches are rejected.

The five utterances include short replies, a time, an abbreviation, a curly apostrophe and a dash. Human listening is still required for audible quality, correct words and endings; nonzero PCM is insufficient. The owner was given a sample and asked whether Alba should proceed to Android integration. No answer is assumed. A positive preliminary choice still requires the full listening set and the actual final Android artifact on both free phones.

No app backend, appearance, signing key, APK or AAB was changed. The current Flite backend remains until a replacement supplies its complete privacy, lifecycle, cancellation, text and playback responsibilities. Small-model LiteRT conversion remains unperformed; the published community graphs for the older large model are not substitutes.

Model/tokenizer and selected Alba voice have CC BY 4.0 attribution recorded in NOTICES.txt and result.json. Kyutai code is MIT. These files contain generated audition audio and sanitized runtime records, not voice source recordings, model weights, private signing videos or credentials.
