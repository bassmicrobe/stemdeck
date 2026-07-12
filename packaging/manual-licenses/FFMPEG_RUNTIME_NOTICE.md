# FFmpeg runtime downloads

LayerLab installers/runtime archives do not intentionally bundle an FFmpeg or
ffprobe executable. First-run setup downloads the executable directly from the
configured provider, verifies it, and stores the source URL in the local setup
configuration.

## macOS

The default macOS source is the versioned evermeet.cx `8.1.1-tessus` build.
Its published configuration contains `--enable-gpl` and `--enable-version3`,
so the resulting executable is a GPL version 3 or later build. It statically
includes multiple external libraries with their own notices and terms.

- Build information: https://evermeet.cx/ffmpeg/info/ffmpeg/8.1.1
- Provider remarks/configuration: https://evermeet.cx/ffmpeg/
- FFmpeg legal page: https://ffmpeg.org/legal.html
- Corresponding FFmpeg release source: https://ffmpeg.org/releases/ffmpeg-8.1.1.tar.xz

## Windows

The default Windows source is the gyan.dev release essentials archive. The
archive is fetched from the provider during setup and is not part of the
LayerLab installer. The provider identifies its full/release builds as GPL.

- Provider/build information: https://www.gyan.dev/ffmpeg/builds/
- FFmpeg source: https://ffmpeg.org/download.html#get-sources

Anyone who mirrors, rebundles, or otherwise conveys an FFmpeg executable must
independently satisfy the applicable GPL/LGPL source, notice, and external
library obligations for that exact build. A link to an unrelated or different
FFmpeg source tree is not a substitute for corresponding source.

