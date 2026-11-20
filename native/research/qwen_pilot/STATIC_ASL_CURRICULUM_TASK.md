# ASL teaching-material transfer task

Continue Sign To Speak for general everyday ASL using a project-trained small Qwen, fully local Android inference, and zero collection of customer camera, microphone, text, landmarks or corrections.

Investigate free ASL alphabet sheets, school lesson materials, individual hand photographs, drawings and captioned teaching videos. Verify the original asset-level license, author, exact version, source bytes and permitted uses; free viewing alone is insufficient. Check embedded media separately from the surrounding course license. Prefer public-domain or clearly reusable original material and project-authored annotations.

State what each example can actually teach. Treat clean stills as a possible auxiliary task for static handshape and orientation. Use real sequences for J/Z, repeated letters, transitions, movement, facial/body grammar and sentence meaning. Check that labels describe ASL. Do not turn English glosses or alphabet worksheets into supposed sentence-translation ground truth.

Keep charts as annotation references until isolated hand inputs have been independently label-checked. Exclude printed answers, arrows, viewpoint notes, filenames, grid position and adjacent examples from model input. Inspect transparency and viewpoints. Exclude the extra I-love-you cell and J/Z from static-letter scoring. Keep original artwork, recordings, bursts and all derivatives together across splits; augmentations do not count as new independent examples.

Before claiming transfer, freeze an independent real-video assessment with reliable references, spelling annotations and documented source/signer separation where available. Keep unknown separation explicit. Add unrelated ordered videos, temporal reordering and blank inputs as diagnostic controls. Record generation completion/token-cap status. Use character and word accuracy for spelling; token overlap alone cannot establish preservation of sentence meaning. The current four reused development clips cannot settle whether static teaching data helps or hurts.

Compare video-only training with a small, task-labelled static auxiliary loss. Match initialization, ordered video batches, video exposure, loss weight, optimizer updates and evaluation. Use a separate deterministic static sampler. Record extra compute and gradient/clipping effects. Do not replace video batches and call that a controlled comparison. Start with a low auxiliary weight and repeat only if the exploratory evidence warrants it.

Keep the auxiliary curriculum only if independent real-video spelling improves without an unacceptable main-task regression. If evidence is inconclusive, say so; drawing recognition and chart memorization do not qualify phone-camera translation. Do not choose a winner from four reused clips or apply a research-only resource to a shipping model without clearing its recorded use boundary.

Stay within the remaining US$14 Hugging Face authorization. Use the dedicated private Sign To Speak bucket and project job label. Preserve failed attempts and receipts. Keep raw samples, generated outputs, credentials and signing keys out of GitHub. Run lightweight local checks, use cloud compute for training and GitHub for application builds, and do not interrupt occupied phones.

Deliver the admitted/reference-only/rejected candidate register; a preregistered paired experiment; measured results when the prerequisites exist; and a keep/remove/inconclusive decision with its evidence limits. Preserve the app's appearance and behavior.
