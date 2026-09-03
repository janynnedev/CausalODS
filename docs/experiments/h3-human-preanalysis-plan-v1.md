# H3 human-evaluation pre-analysis plan v1

Status: frozen before ratings. No human ratings had been collected or inspected
when this plan was created.

The unit of experimental comparison is incident × explanation condition. Each
item receives two independent ratings. For each item and rubric dimension, its
score is the arithmetic mean of those two ratings. Every incident therefore
contributes three paired item scores, one per hidden condition.

For every condition and each of utility, clarity, perceived confidence,
traceability, and uncertainty adequacy, report the number of incidents, mean,
standard deviation, median, Q1, and Q3. Do not create a post-hoc composite of
the five dimensions.

The primary inferential analysis is a repeated/paired comparison of the three
conditions blocked by incident. Because the ratings are ordinal, run a Friedman
omnibus test separately for each rubric dimension. If that dimension's omnibus
comparison is significant, run paired Wilcoxon signed-rank comparisons for all
three pairs (A–B, A–C, B–C) and apply Holm correction. The tests will not be
changed after observing results.

Exact percent agreement may be reported descriptively for compatibility with
the current reporting pipeline. Any additional reliability coefficient must be
specified and documented before the ratings lock is unblinded. Missing or
invalid rating data fails validation and is not silently imputed. H3 must never
be automatically labeled “confirmed”; conclusions require interpretation of the
pre-specified results and limitations.
