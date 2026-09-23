# Scientific method and validation

## Interpretation boundary

Brain Cell Counter reports **object-based co-expression inside DAPI-defined
objects**. It does not measure pixel-level colocalization.

NeuN, c-Fos, and DAPI thresholds require validation against controls,
intensity distributions, segmentation masks, and manual review. Automated
parameter suggestions and stable-plateau selection are decision aids, not
biological ground truth.

## Spatial calibration

Physical calibration changes measured area, distance, density, Cellpose scale
interpretation, and ABBA alignment. The calibration wizard therefore separates
automatic endpoint proposal from human approval. It never selects a physical
distance automatically: the reviewer must read the scale-bar label, verify or
redraw both endpoints, enter the distance and unit, and confirm the proposal.

The calculation is:

```text
pixel size (µm/pixel) = printed distance (µm)
                        / (preview bar length × preview downsample)
```

Existing metadata is displayed for comparison. A difference greater than 5%
requires an additional confirmation. Different images may legitimately have
different calibration, and the software does not force a batch-wide value.
Calibration updates QuPath metadata only; it does not create new image pixels
or improve the underlying optical resolution.

Regional area and density outputs are enabled only when both calibrated pixel
dimensions are finite and greater than zero. `Region_Area_mm2` is the summed
geometry area of mutually exclusive Allen leaf annotations for one region and
hemisphere. Density outputs are two-dimensional detected-object profiles per
mm². They must not be interpreted as unbiased cells/mm³ or total cell-number
estimates; those require an appropriate stereological sampling design.

## Global-threshold optimizer

**FIND GLOBAL THRESHOLD** uses existing Cellpose intensity measurements. It
does not rerun Cellpose and treats channels generically; saved marker names do
not change the calculation.

Available objectives are:

- Channel 1 positive + Channel 2 positive
- Channel 1 positive + Channel 3 positive
- Channel 2 positive + Channel 3 positive
- Individual channels

For a channel-pair objective, the optimizer first finds a valid dataset-wide
stable plateau for both channels, then tests every threshold pair inside those
plateaus. It selects the pair with the largest double-positive count. Ties are
resolved toward higher, more selective thresholds.

Searching only inside stable plateaus is intentional. Searching the complete
numeric range would usually select low thresholds because background and noise
inflate apparent double-positive counts.

For individual channels, the software selects the highest threshold within the
longest qualifying stable plateau.

## Background/positive separation guard

The automatic guard is enabled by default. For each channel, it reconstructs a
dataset-wide intensity histogram from the threshold count curve and applies
Otsu between-class separation. Thresholds below the split are excluded so a
saturated low-threshold plateau cannot be selected as a positive population.

The report records the automatic split, positive fraction, separability score,
and whether the selected value came from a stable post-background plateau or
an automatic-split fallback. Weak separation generates a warning and requires
visual review.

## Analysis scopes

- **Current project — all entries** uses every section in the open project.
- **Selected entry only — test** evaluates one section but intentionally does
  not allow that result to be locked as a global threshold.
- **Chosen project file — all entries** uses every entry in a selected
  `project.qpproj`.

All sections belonging to a dataset-wide optimization should be included in
the intended QuPath project. Every included entry must already have Cellpose
detections with the required channel mean-intensity measurements.

## Default plateau rules

```text
total count change < 3%
at least 90% of sections have section count change < 5%
at least 3 consecutive stable threshold steps
```

## Threshold reports

The optimizer writes:

```text
global_threshold_optimization_v2.4.csv
global_threshold_optimization_v2.4.json
global_threshold_optimization_v2.4.html
```

Pair objectives also write:

```text
channel_pair_double_positive_optimization_v2.4.csv
```

Reports include total counts, per-section counts, percentage changes,
stable-section percentages, plateau IDs, and the selected row. After a complete
project scan, the GUI can apply and lock all three thresholds. Unlock them only
when intentionally starting a new dataset-level optimization.

## Atlas validation baseline

The tested project validated with:

- 433 atlas annotations
- 275 leaf regions
- 1,327 ontology entries
- 275 of 275 matching leaf IDs

Cellpose-SAM 4.2.1.1 was smoke-tested through the headless QuPath engine. These
figures document one tested dataset and do not guarantee correctness for a new
dataset.

The application exposes both `cpsam` and `cpsam_v2`. Changing the model changes
the segmentation population and therefore requires representative-ROI mask
review before a batch. Existing detections are never claimed to have been
created by a newly selected model. Intensity classification remains based on
measurements from the original image, not restored or synthesized pixels.

## Curated functional subregions

Allen atlas names and IDs remain the primary anatomical result. The optional
`Functional_Subregion` column adds a transparent analysis grouping without
renaming or merging the Allen rows. Definitions are read from the versioned
`config/functional_subregions_v1.json`. Schema 2.4 includes mPFC; motor,
somatosensory, visual, auditory, and retrosplenial cortex; hippocampal CA,
dentate gyrus, and subiculum complex; basolateral, central, and medial amygdala;
dorsal and ventral striatum; and four hypothalamic groups. Every descendant of
a configured Allen root acronym inherits that label.

The engine rejects overlapping group definitions rather than silently choosing
one. `Functional_Subregion_Config` records the mapping schema and SHA-256 in
every CSV. `ORBm` and `MOs` remain excluded from mPFC because the literature
does not use one universal rodent mPFC boundary. Any mapping change requires
biological review before datasets are combined.

## GPU validation baseline

The tested Cellpose environment used PyTorch 2.11.0+cu128 and torchvision
0.26.0+cu128 on an NVIDIA GeForce RTX 3050 Laptop GPU. CUDA was checked with a
tensor test and through the QuPath Cellpose engine. Other computers require
their own installation verification.
