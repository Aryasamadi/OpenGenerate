# ADR-0010 — Media pipeline and storage

**Status:** Accepted

## Decision
- **Extraction sources (priority):** feed enclosure/media tags → JSON-LD `image/video` → Open Graph/Twitter → in-article `<img>/<video>/<source>` → alternates. Candidates are scored (dimensions from headers/probing, aspect ratio, URL heuristics); logos, avatars, sprites, tracking pixels, <min-size, wrong MIME, and unsafe URLs are discarded.
- **Fetching** only through `SafeHttpClient` (ADR-0009): size cap (default 10 MB photo / 50 MB video per Telegram limits), total time cap, MIME sniffing (magic bytes, not just header), image re-encode/strip metadata optional.
- **Delivery order:** try Telegram `url` fetch → if rejected, download+upload → next alternate → text-only fallback (policy-controlled per channel). Media and caption remain one logical message where Telegram permits; if the caption exceeds the limit, the compact teaser goes in the caption and the "more" deep link carries the rest.
- **Storage:** transient temp dir (`MEDIA_TMP_DIR`, tmpfs/volume) with per-file and total-bytes caps and age-based janitor; **no durable media storage and no object store**. Receipts: Telegram `file_id` + metadata only. A `MediaStore` port exists so S3-compatible storage is an additive change.
- **AI illustration:** *not part of v1*. Hook is a disabled-by-default `IllustrationProvider` port; enabling would require a separate ADR (provider config, moderation, cost accounting, cache, opt-in switch).

## Consequences
Disk use is bounded and measurable; the container needs a writable tmp volume only.

## Revisit trigger
Requirement to retain media archives or serve media from our own host.
