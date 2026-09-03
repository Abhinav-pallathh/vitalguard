# Audio

`ambient.ogg` is deliberately **not** committed — its licence is unconfirmed and
this repository is public. The game checks for it and runs silent when it is
absent, so a fresh clone works without it.

To restore the soundtrack, drop any looping ambient track here as
`ambient.ogg`. The game plays the whole file on loop, starting on the first
keypress (browsers block autoplay before a user gesture).

Ours is the first 50 seconds of a track, trimmed with:

```bash
ffmpeg -i input.mp3 -t 50 \
  -af "afade=t=in:st=0:d=0.15,afade=t=out:st=49.85:d=0.15" \
  -c:a libvorbis -q:a 3 ambient.ogg
```

The two 0.15 s fades stop an audible click at the loop seam.
