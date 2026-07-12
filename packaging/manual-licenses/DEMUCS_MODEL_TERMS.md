# Demucs pretrained model weights

The Demucs source code is distributed under the MIT License. The pretrained
model weights downloaded by Demucs are a separate artifact and are not covered
by that MIT code license.

In official Demucs repository discussions, a Demucs maintainer states that the
weights are provided for personal/research usage because the MUSDB training
dataset is restricted to research use. The maintainer also describes commercial
use of those weights as an unresolved legal-risk decision rather than an
expressly licensed use.

Primary references:

- https://github.com/facebookresearch/demucs/issues/327#issuecomment-1134828611
- https://github.com/facebookresearch/demucs/issues/384#issuecomment-1262197483
- https://sigsep.github.io/datasets/musdb.html

LayerLab does not bundle the Demucs weights in its installer/runtime archive.
The unmodified Demucs downloader obtains them on first separation. This
technical separation does not grant additional model rights.

Do not describe the pretrained weights as MIT-licensed or approved for
commercial use. Before a commercial/public service release, obtain permission
for the selected weights, replace them with weights whose terms permit the
intended use, or disable automatic pretrained-weight use.
