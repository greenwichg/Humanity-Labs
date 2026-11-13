# Community: infrastructure photos, assessments, notes and discussion

Community (`/community`) lets people share photos of public infrastructure in India: roads, highways, flyovers, bridges,
dams, government schools, drainage, public water infrastructure, railway infrastructure, public buildings and other
public works. Each photo is checked, assessed by open-source computer vision, placed in its constituencies, linked to
official project records only on evidence, and opened to Community Notes and a threaded discussion.

The live methodology page, `/community/about`, shows every model, threshold and rule from the code itself. This document
covers the same ground for developers and operators, plus how to run it.

## Four separate things

| Component | What it is | What it never does |
|---|---|---|
| Image assessment | What the models see in one photo, as a visible-condition score with criteria, evidence and confidence | Judge safety, structure or hidden defects |
| Community verification | Community Notes, with statuses from ratings across perspectives | Change a score |
| Scores and rankings | Averages of image assessments per category and place | Combine categories |
| Discussion | Threaded comments with votes | Change an assessment or a note |

Official project records (costs, agencies, contractors) come from Infrastructure Transparency, keep their own evidence
labels and sources, and are shown apart from anything contributors submit.

## How a photo becomes a post

1. **Upload (web process, under a second).** `community/service.py` and `community/vision/image.py`: JPEG, PNG or
   WebP, at most 12 MB, at least 320 px on the shorter side. The image is rotated as the camera intended and saved again
   without any metadata. A GPS position in the file is offered as the post's location and never kept in the image.
   Photos too blurred or too dark to show anything are refused; low-resolution, blurred, dark, washed-out or flat photos
   are kept but not scored.
2. **Duplicates.** `community/duplicates.py`: the same file (SHA-256), the same pixels in another format (SHA-256 of the
   decoded image), or a near copy (64-bit perceptual hash within 8 bits *and* difference hash within 10 bits) is refused,
   and the earlier post is linked.
3. **Processing (worker; measured 11 s for a 640-px photo and 33 s for a 2048-px street photo with model loading, on 4 CPUs).** `community/pipeline.py`, every 15 s from the worker or
   `python -m batchlight.cli community-process`:
   - faces and number plates are blurred first (oversized boxes, usually a whole person or vehicle, are looked at again
     so only the face or plate is blurred);
   - the assessment runs (`community/assess.py`);
   - a crop or second photo of the same spot by the same contributor (same category, within 100 m and 24 h, CLIP
     similarity ≥ 0.88) is marked as a repeat: it stays visible and counts once in rankings;
   - representatives are tagged and official records are checked;
   - the post is published. Until the models are installed, posts wait in processing and nothing unblurred is shown.

## Models

All four run on CPU with ONNX Runtime (no PyTorch), on our own servers; photos are not sent to any outside service and
ONNX Runtime's usage telemetry is switched off. Each file is pinned to a revision and checked by SHA-256
(`community/vision/models.py`). About 700 MB in all.

| Use | Model | Licence |
|---|---|---|
| Kind of infrastructure, visible condition | `Xenova/clip-vit-base-patch32` (OpenAI CLIP ViT-B/32) | MIT |
| Possible defects; faces and plates to blur | `Xenova/owlv2-base-patch16-ensemble` (Google OWLv2) | Apache-2.0 |
| Damage-like regions | `Xenova/clipseg-rd64-refined` (CLIPSeg) | Apache-2.0 |
| Plain-language description | `onnx-community/Florence-2-base-ft` (Microsoft Florence-2) | MIT |

Sentences in the description about safety, structure, risk or failure are removed before it is stored.

## Scores

Each category has its own descriptions of good, fair and poor condition, its own defect list and its own damage
descriptions (`community/categories.py`). A score starts at 100:

- **Visible condition** removes up to 70 points: 70 × (poor + 0.4 × fair), from CLIP.
- **Damage-like regions** removes up to 30 points, growing with the share of the photo CLIPSeg outlines (probability over
  50%), full at 20%.
- **Possible defects** from OWLv2 (confidence ≥ 30%) are drawn on the photo but carry no points.

Confidence = 0.35 × photo quality + 0.35 × category fit + 0.30 × agreement between the condition rating and the
outline. A photo is not scored when it is not clear enough or when the chosen category fits under 15% of the best
match. Each post lists every criterion with its evidence and method, the model versions and the engine version.

### How the thresholds were set

Measured on 80 road photos with 110 potholes outlined by hand (the train split of
[keremberke/pothole-segmentation](https://huggingface.co/datasets/keremberke/pothole-segmentation), Roboflow, CC BY 4.0)
and 21 ordinary street photos ([BabaDZON/everyday-life-scenes](https://huggingface.co/datasets/BabaDZON/everyday-life-scenes), CC0).
The photos were used only for measurement and are not in the repository.

| Measure | Result | Decision |
|---|---|---|
| Kind of infrastructure | 80/80 pothole photos recognised as roads | Category fit is reliable for roads |
| Visible condition | "poor" > 50% for 96% of pothole photos, 5% of street photos | Main criterion, 70 points |
| OWLv2 at 30% | a pothole box on 17% of pothole photos, a defect box on 19% of street photos; 44% of pothole boxes matched a labelled pothole | Shown, not scored |
| CLIPSeg at 50% | mean overlap with labelled potholes 27% (best of 30–60%); outlined size did not follow the true size (correlation 0.09); nothing outlined on most street photos | Presence signal, 30 points, full at 20% |
| Resulting scores | median 15 for pothole photos, 76 for street photos | |

Limits: the set is small, the damage in it is obvious, and only roads were tested. Mild wear and the other categories
need their own labelled photos before their thresholds can be called calibrated. Re-run the measurement when models or
prompts change, and bump `ENGINE_VERSION`.

## Rankings

`community/rankings.py`. Per category, at village, town, city, state and country level; never across categories. A
place is ranked only with at least 5 scored photos from at least 2 contributors; every figure shows its count, a 95%
margin of error, average confidence, coverage label and a 12-month trend. Repeats and hidden posts are excluded.
Rankings describe only the photos people chose to share, not a survey.

## Community Notes

`community/notes.py`, after the open-source method of X's Community Notes. Ratings (helpful 1, somewhat 0.5, not
helpful 0) are fitted as `μ + rater + note + rater_factor × note_factor` by alternating least squares with a fixed seed
(regularisation 0.15 on intercepts, 0.03 on factors). Statuses:

- **Pending**: fewer than 5 ratings.
- **Helpful**: note intercept ≥ 0.40, |note factor| < 0.50 and at least 2 "helpful" ratings from each side of the factor axis.
- **Not helpful**: intercept ≤ −0.05 − 0.8 × |factor|.
- **Disputed**: ratings split along the factor axis (|factor| ≥ 0.50 and the sides differ by ≥ 50 points).
- **Insufficient consensus**: anything else.

A note's author cannot rate it; editing a note clears its ratings; every status change is kept. A young community
(few raters, few notes) will mostly see Insufficient consensus, as the method intends.

## Elected representatives

`community/reps.py`. The post's position is placed in an assembly constituency using the
[DataMeet assembly constituency map](https://github.com/datameet/maps) (CC BY 4.0), and in its Lok Sabha seat through
the map's parliamentary constituency number. The result is shown as "representatives for this area", never as
responsible for the work. Seats from the map's pre-delimitation boundaries, or filed under a different state from the
post (the map predates Telangana's formation), are flagged. Ward councillors and sarpanches are listed as not available.

**Names need an approved source.** No roster is bundled. `python -m batchlight.cli community-roster FILE.csv` loads one
from a CSV with columns `kind,state,number,name,party,term_start,term_end,source,source_url` (`kind` is `assembly` or
`lok_sabha`, `state` a state slug, `number` the constituency number). Every row must name its source. Until a roster is
loaded, the post shows the constituency and "Representative not loaded yet".

## Official project records

`community/projects.py`. A link is made only when the reference number the contributor read on site (tender reference,
PMGSY package number or project code) matches the official record ignoring spaces and punctuation, the record's state
includes the post's state, and the record's type of work fits the category. Similar names or places are shown as
"might be related", unlinked. Financial facts shown with a link come from the record with their source and evidence
label, and carry this caveat: they are for the whole project or contract, not the section in the photo, and say nothing
about its condition.

## Moderation

Anyone signed in can report a post, note or comment. Curators (`users.is_curator`) see `/community/moderation` and can
hide or dismiss; hiding keeps the record for audit and removes it, and its photo, from public view.

## Running it locally

```bash
python -m batchlight.cli community-models download    # ~700 MB, checked by SHA-256
python -m batchlight.cli community-boundaries          # DataMeet constituency map (18 MB)
python -m batchlight.cli community-roster roster.csv   # optional, from a source you have approved
python -m batchlight.cli community-process             # or run the worker, which does this every 15 s
```

Settings (environment variables, prefix `BATCHLIGHT_`): `COMMUNITY_UPLOADS` (default: on outside production, off in
production), `MEDIA_DIR` (local photo folder, default `media/`), `MODEL_DIR` (default `~/.cache/humanity-labs/models`),
`VISION_THREADS`, `COMMUNITY_POSTS_PER_DAY` (20), `COMMUNITY_COMMENTS_PER_HOUR` (60), `COMMUNITY_NOTES_PER_DAY` (10).

## Turning on uploads in production

Uploads are off in production and nothing in AWS has been created for them. Turning them on needs the owner's approval
and these steps:

1. **Storage.** `infra/terraform/platform/media.tf` designs a private bucket (`humanity-labs-media-<account>`):
   versioned, SSE-S3 encrypted, all public access blocked, HTTPS only, replaced versions kept 7 days (so unblurred
   originals do not linger). It is created only with `community_media = true`. The app stack then adds a task role that
   can read, write and delete objects under `posts/` in that bucket only, and the bootstrap stack's permissions boundary
   and deploy role allow exactly that. Offline tests cover all of it (`terraform test` in each stack).
2. **S3 client.** The app's photo store is local disk today. If `BATCHLIGHT_MEDIA_S3_BUCKET` is set before an S3 store
   is written, the app refuses to start storing photos rather than fall back to the container's disk.
3. **Worker size.** Measured peak memory with all four models loaded is 4.2 GB, plus 700 MB of model files. The
   worker task needs about 2 vCPU / 6 GB (today's is 0.25 vCPU / 2 GB), and the models must be downloaded at start or
   baked into a worker image.
4. **Roster.** Approve a source for MP and MLA names and load it.
5. Set `BATCHLIGHT_COMMUNITY_UPLOADS=true`.

Estimated extra cost in ap-south-1, before 18% GST: S3 storage at about $0.025 per GB-month (a stored photo is about
0.5 MB, so 10,000 photos ≈ 5 GB ≈ $0.15/month), and the larger worker on Fargate ARM64: at the rates in
[AWS_DEPLOYMENT.md](AWS_DEPLOYMENT.md#estimated-monthly-cost) ($0.02383/vCPU-h, $0.00261/GB-h), 2 vCPU / 6 GB is about
$46/month on-demand, about $38 more than today's worker (less on Spot). Check current prices before approving.

## Tests

- `tests/unit/test_community.py`: photo checks, metadata removal, hashes, blurring, scoring, category criteria,
  description cleaning, Community Notes statuses, point-in-polygon, reference matching and link evidence, storage safety.
- `tests/integration/test_community.py`: the full flow against PostgreSQL with a stub in place of the models: share,
  process, duplicates, notes and discussion leaving the score unchanged, rankings thresholds and category separation,
  repeats, moderation, the production switch and the methodology page.
