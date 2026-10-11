# City Scan method

> **Shipped configuration (after `ABLATION.md`, section 9).** The ranking score is the point estimate of
> suitability (section 4: sign-constrained MAP logistic regression on own-cell counts, OSM + Overture) among cells
> at or above the city median. Kernel smoothing of features and demand, empirical-Bayes shrinkage of the point
> features, Huff captured demand and the agglomeration term were each tested and did **not** improve the ranking;
> they are kept as display (interval width, Huff share, captured demand for the break-even scenario) or as
> switches in `cityscan.DEFAULT_OPTS`. Sections 1–6 describe every component, shipped or not.

City Scan answers "where in this city should my concept open?" with OpenStreetMap data, optionally cross-checked
against Overture Maps Places in the offline pack builder (section 1b). Every step below is a
pure function in `cityscan_math.py` (maths) or `cityscan.py` (OSM signals, pipeline), tested on synthetic data in
`tests/test_cityscan_math.py` and `tests/test_cityscan.py`. Standard library only; random draws use fixed seeds.

## 0. Data and grid

- One bounded Overpass query per city (bbox from Nominatim, clipped to 20 km around the city centre). Signals:
  offices and coworking, universities, schools, rail/metro/tram stations, bus stops, malls and markets, hotels,
  attractions, nightlife, cinemas and theatres, gyms, residential and commercial land use (hectares, from way
  bounding boxes), every food and drink venue, and suburb/neighbourhood names.
- Grid of square cells, 400 m by default, at most 2,500 cells (cells grow if needed). Local equirectangular projection
  ($k_y = 110\,570$ m/°, $k_x = 111\,320\cos\varphi_0$ m/°).
- Study area $U$: cells with at least one mapped object within the kernel's reach (excludes sea and empty land).

## 1. Counts with empirical-Bayes uncertainty

For each signal $g$ and cell $i$, the mapped count $n_{ig} \sim \text{Poisson}(\lambda_{ig})$ with a city-level prior
$\lambda_{ig} \sim \text{Gamma}(a_g, b_g)$ fitted by the method of moments over $U$ (Casella 1985):

$$\bar n = a/b,\qquad s^2 = \bar n + \bar n / b \;\Rightarrow\; b = \frac{\bar n}{s^2 - \bar n},\quad a = \bar n\, b
\quad (s^2 > \bar n;\ \text{otherwise } b \to \infty).$$

Posterior: $\lambda_{ig} \mid n_{ig} \sim \text{Gamma}(a_g + n_{ig},\ b_g + 1)$, mean
$\frac{b}{b+1}\bar n + \frac{1}{b+1} n_{ig}$, i.e. small, noisy counts shrink toward the city mean. 90% intervals use
the Wilson–Hilferty approximation. Land use (hectares) is treated the same way as a continuous count.

## 1b. Second source: Overture Places, coverage gap, completeness, capture–recapture

**Source.** `cityscan_overture.py` (offline builder only; needs DuckDB in a throwaway venv, the app stays stdlib)
runs one DuckDB query per city against the public bucket
`s3://overturemaps-us-west-2/release/<latest>/theme=places/type=place/*` (anonymous, `s3_region='us-west-2'`,
`threads=2`, `memory_limit='1GB'`), filtered on the `bbox` struct and on food/office/education/transit/retail/
hotel/culture/fitness categories. Category = `coalesce(taxonomy.primary, basic_category)` (the old `categories`
column is gone in 2026 releases). `overture_tags()` maps each category onto the OSM tags the model already reads
(e.g. `korean_restaurant` → `amenity=restaurant, cuisine=korean`; `coffee_shop` → `amenity=cafe`;
`college_university` → `amenity=university`; `*_company`/`*_agency`/`b2b_*` → `office=company`). Licence
CDLA-Permissive-2.0, attribution "© Overture Maps Foundation" plus the per-place source (Meta, Foursquare,
Microsoft, AllThePlaces, PinMeTo; counts kept in the pack).

**Matching / de-duplication.** Within a match group (all food and drink together; otherwise per category) an OSM
point $a$ and an Overture point $b$ are the same venue when $d(a,b) < 50$ m and

$$\text{sim}(a,b) = \max\Big(\text{SequenceMatcher}(a,b),\ \tfrac{|T_a\cap T_b|}{|T_a\cup T_b|},\
0.9\cdot\mathbb 1[T_\text{short}\subseteq T_\text{long}]\Big) \ge 0.6,$$

on accent-folded names stripped of generic words (café, restaurant, snack, le/la/el…; tokens $T$, containment needs
tokens of ≥3 characters, which handles bilingual "nafoura النافورة" vs "Café Nafoura"). Unnamed pairs need
$d < 20$ m. Greedy best-first, one-to-one. Matched Overture points take the OSM category; the **union**
(OSM + unmatched Overture) is the count used everywhere downstream.

**Coverage gap and completeness** per cell $i$ and compare category $g$ (restaurant, café, bar, office,
university, school, station, mall, hotel, attraction, nightlife, culture, fitness):

$$\text{gap}^{\text{OSM}}_{ig} = 1 - \min\!\Big(1, \frac{n^{\text{OSM}}_{ig}}{n^{\text{Ov}}_{ig}}\Big),\qquad
\text{gap}^{\text{Ov}}_{ig} = 1 - \min\!\Big(1, \frac{n^{\text{Ov}}_{ig}}{n^{\text{OSM}}_{ig}}\Big),\qquad
c_i = \frac{1}{|G_i|}\sum_{g\in G_i} \frac{\min(n^{\text{OSM}}_{ig}, n^{\text{Ov}}_{ig})}{\max(n^{\text{OSM}}_{ig}, n^{\text{Ov}}_{ig})},$$

$G_i$ = categories with at least one point in either source (cells with none take the city mean). City **data
confidence** = mean of $c_i$ over study cells (also reported for food categories only).

**Capture–recapture** (Chapman 1951), treating the two sources as two surveys of the same closed population:

$$\hat N = \frac{(n_1+1)(n_2+1)}{m+1} - 1,\qquad
\widehat{\text{Var}}(\hat N) = \frac{(n_1+1)(n_2+1)(n_1-m)(n_2-m)}{(m+1)^2(m+2)}\ \ (\text{Seber 1982}),$$

missed by both $= \hat N - (n_1 + n_2 - m)$, detection of the union $q = (n_1+n_2-m)/\hat N$. Assumptions: the two
sources detect venues independently, and matching is accurate. Both are violated in a known direction: sources
copy each other (positive dependence → $\hat N$ too low), while missed matches from coordinate disagreement > 50 m
or different names push $\hat N$ up. Detection $q$ is used **only for food** (restaurants, cafés, bars), where both
sources survey the same population; offices, schools and attractions are defined differently in the two taxonomies
(Casablanca: 12 matched offices out of 441 + 806), so their Chapman numbers are reported, not used.

**Feeding the uncertainty.** Counts are now $n_{ig} \sim \text{Poisson}(q_g\lambda_{ig})$ (exposure $q_g$; 1 where
not estimated). Method of moments gives $\mathbb E\lambda = \bar n / q$, $\text{Var}\,\lambda = (s^2 - \bar n)/q^2$.
The posterior is tempered by the cell's completeness (a power prior/likelihood, Ibrahim & Chen 2000):

$$\lambda_{ig}\mid n_{ig} \sim \text{Gamma}\big(\kappa_i (a_g + n_{ig}),\ \kappa_i (b_g + q_g)\big),\qquad
\kappa_i = \max(0.2,\ c_i).$$

Same posterior mean, variance multiplied by $1/\kappa_i$: where OSM and Overture disagree, every Monte Carlo draw of
that cell's counts spreads wider, the 90% opportunity interval widens and the 5th-percentile rank drops. Layers with
no Overture counterpart (bus stops, land use) keep $\kappa = 1$.

## 2. Demand surface

$$D_i = \sum_g w(g)\, \mathbb{E}\left[K(d_{i,g})\right],\qquad K(d) = e^{-d/\lambda},\ \lambda = 400\text{ m (walking)},$$

truncated at $3\lambda$ (5% residual). On the grid the expectation is taken over a $5\times5$ lattice inside the source
cell (so the own cell gets $\mathbb{E}[K] < 1$, not $K(0)=1$); the grid itself is the spatial hash, so the cost is
$O(\text{cells} \times \text{stencil})$, not $O(N M)$. Point queries use a bucket index (`GridIndex`).

$w(g)$ = trip-attraction prior per object (one office = 1, a station or a mall = 5, a university site = 5, a bus stop
= 0.5, a hectare of housing = 0.5...) times the concept profile's relevance / 2. **This is the one stated prior in the
opportunity score**; the suitability weights are learned (section 3).

## 3. Suitability: presence-background logistic regression

Existing venues of the concept are presences (the MaxEnt / species-distribution analogy: where can this "species" of
restaurant survive?), study cells are background (Phillips et al. 2006). Features: standardised
$x_{ik} = \log(1 + \text{kernel density})$ for offices, universities, schools, transit ($3\times$ stations + bus
stops), malls, hotels, attractions, nightlife, culture, fitness, residential land, commercial land, and other food
venues (the concept's own venues are excluded, so presences never leak into features).

$$\hat\beta = \arg\min_\beta\ -\sum_r w_r\left[y_r \log\sigma(\eta_r) + (1-y_r)\log(1-\sigma(\eta_r))\right]
+ \tfrac{\ell_2}{2}\lVert\beta_{1:p}\rVert^2,\qquad \eta = \beta_0 + \beta^\top z,$$

background weights $n_\text{pres}/n_\text{bg}$ (balanced), $\ell_2 = 1$, intercept unpenalised. With background
weight growing, weighted logistic regression is equivalent to the inhomogeneous Poisson process / MaxEnt model up to
the intercept (Fithian & Hastie 2013), so $\sigma(\eta)$ is a **relative** suitability, not an absolute probability.
Solved by Newton–Raphson (IRLS), a second-order descent on the convex loss: same optimum as gradient descent in ~10
iterations instead of thousands. Coefficient covariance by the Laplace approximation
$\Sigma = H(\hat\beta)^{-1}$ (reported as `coefficient_se`).

**Fallback** when a city has fewer than 15 presences: $\beta_k = 0.25 \times$ relevance (0.5 for other food),
rescaled so the city's median $|\eta|$ is 1, with $\Sigma = 0.5^2 I$. The output says so (`model.fitted = false`,
`model.note`), and unit cards rank such concepts after validated ones.

**Validation**: spatial block cross-validation, $k = 5$ contiguous bands along the grid's longer axis, never random
splits (Roberts et al. 2017). Pooled out-of-fold AUC, Mann–Whitney form with ties counted half:

$$\text{AUC} = \frac{R_+ - n_+(n_+ + 1)/2}{n_+ n_-},$$

95% percentile bootstrap CI with 200 stratified resamples (presences and background resampled separately).

**Explanation**: the logit is exactly additive, $\eta_j = \beta_0 + \sum_k \beta_k z_{jk}$; the three largest positive
terms $\beta_k z_{jk}$ are shown with the raw mapped counts within 600 m (EN and FR), and the largest negative term as
the main risk.

## 4. Captured demand: Huff gravity model

Huff (1963): a consumer at demand cell $i$ chooses site $j$ among the candidate and the existing same-concept venues
$k$ with

$$P_{ij} = \frac{A_j^\alpha d_{ij}^{-\beta}}{A_j^\alpha d_{ij}^{-\beta} + \sum_k A_k^\alpha d_{ik}^{-\beta}},
\qquad \alpha = 1,\ \beta = 2,\ d \ge 50\text{ m},\ A = 1,$$

demand cells within 2 km; for $i = j$ the distance is the mean distance from a uniform point in a square to its
centre, $0.3826 \times$ side. Captured demand $E_j = \sum_i D_i P_{ij}$ and the Huff share
$E_j / \sum_i D_i$. Cannibalisation is explicit: every existing competitor near a demand cell lowers $P_{ij}$.

## 5. Agglomeration: local Ripley's K / L

At the candidate $j$, with $n$ same-concept venues in the study area of size $A$ and $n_r(j)$ of them within
$r = 300$ m:

$$\hat K_j(r) = \frac{A\, n_r(j)}{n},\qquad \hat L_j(r) = \sqrt{\hat K_j(r)/\pi},\qquad
\text{excess}_j = \hat L_j(r) - r,$$

0 under complete spatial randomness, positive when venues cluster (Ripley 1976; Besag 1977; no edge correction).
Reported separately as `agglomeration.l_excess_m`, and added as
$\gamma \max(0, \tanh(\text{excess}/r))$ only for destination concepts ($\gamma$ = 0.15 fine dining and bars, 0.10
generic restaurants, 0.05 fast casual, 0 for cafés and healthy food, where clustering is cannibalisation, already in
Huff).

## 6. Opportunity, uncertainty and robust ranking

$$\text{Opp}_j = \sigma(\eta_j)\cdot \frac{E_j}{\max_{j'} E_{j'}} + \text{agglomeration}_j \quad(\times 100\text{ in the output}).$$

Monte Carlo, 100 draws, fixed seed, for a candidate pool (the 150 best point estimates): each draw samples every count
$\lambda_{ig}$ from its completeness-tempered Gamma posterior (section 1b) and the coefficients from $N(\hat\beta, \Sigma)$, then recomputes features,
suitability, demand and Huff capture. The first version ranked by the 5th percentile; with wide intervals that sorted
noise (section 9). The interval width is shown as "confidence". *Shipped:* the score is suitability alone (no
$E_j$, no agglomeration), ranked by its point estimate; the Monte Carlo still gives the 90% interval.

## 7. Spatial diagnostics

- **Moran's I** of model residuals $e_i = y_i - c\,\hat p_i$ ($y_i$ = cell has a presence, $c$ calibrates the mean),
  rook contiguity, binary weights (Moran 1950):
  $I = \frac{n}{S_0}\frac{\sum_i\sum_j w_{ij} e_i e_j}{\sum_i e_i^2}$, $E[I] = -1/(n-1)$, variance under randomisation
  (Cliff & Ord 1981):
  $\text{Var}(I) = \frac{n[(n^2-3n+3)S_1 - nS_2 + 3S_0^2] - b_2[(n^2-n)S_1 - 2nS_2 + 6S_0^2]}{(n-1)(n-2)(n-3)S_0^2} - E[I]^2$.
  A significant positive $I$ means the model misses a spatial driver (unmapped amenities, income, rents).
- **Getis–Ord $G_i^*$** z-scores of opportunity, queen contiguity including the cell (Getis & Ord 1992; Ord & Getis
  1995): $G_i^* = \frac{\sum_j w_{ij}x_j - \bar x \sum_j w_{ij}}{S\sqrt{[n\sum_j w_{ij}^2 - (\sum_j w_{ij})^2]/(n-1)}}$.

## 8. Unit → concepts and backtest

- **Unit mode**: for a point, every catalog concept gets suitability, Huff share, Monte Carlo interval,
  percentile versus the city, top reasons, the 3 nearest competitors and a break-even scenario; ranking across
  concepts is in section 9.
- **Backtest** per city and concept: presences, spatial-CV AUC with 95% CI, Moran's I of residuals, and the share of
  real existing venues falling in the top 20% of cells by out-of-fold suitability (random = 20%).

## 9. Corrections round: what each piece must earn (see `ABLATION.md`)

**Two targets, two tests.** Presence of existing venues says where a concept *is*, not where a new one should
open. Suitability is therefore evaluated on presence (5 spatial-band folds, out-of-fold scores), and the
opportunity ranking separately, **forward in time**: the city is rebuilt as of a cutoff (food venues whose first
OSM version is dated after the cutoff are removed, together with Overture places within 50 m of them), the model
ranks cells, and the score is how well it anticipates where venues were first mapped after the cutoff
(OSM `out meta`: `version = 1` and `timestamp`). "First mapped" is not "opened": mapping campaigns and late
mapping of old venues add noise, and elements created after the cutoff but edited since (`version > 1`) stay in
the "existing" set, which makes the test conservative.

**Baselines.** B1: kernel density of every mapped POI (all categories, food included; the target venues excluded
in the presence test). B2: kernel density of the concept's own venues (neighbouring cells only in the presence
test, all existing venues in the forward test): "open where they already are".

**Ablation and the keep rule.** Each component is switched off alone (`cityscan.DEFAULT_OPTS`, `PARAMS["eb"]`,
OSM-only city). Metrics: AUC (cells with $\ge 1$ venue vs none), share of venues in the top 20% of cells, share in
the top 50 cells. Differences full − ablation get a paired, stratified percentile bootstrap (200 resamples of
cells, the same resample for both scores). A component stays in the score only if the 95% CI of its difference
excludes 0 in its favour on the test that concerns it; otherwise it is removed or shown for display only.

**Sign-constrained MAP.** Suitability is now the MAP estimate under a Gaussian prior centred on the stated
weights, with footfall generators constrained non-negative (other food may be negative: competition):

$$\hat\beta = \arg\max_{\beta:\ \beta_k \ge 0\ (k \in G)}\ \ell_w(\beta) - \tfrac{\ell_2}{2}\lVert\beta_{1:p} - \beta^{0}\rVert^2,$$

solved by an active-set loop around Newton–Raphson (pin the most negative constrained slope at 0, refit; release a
pinned slope when the KKT gradient is positive). Laplace covariance on the free slopes; Monte Carlo draws of
constrained slopes are truncated at 0. A reason is shown only when its coefficient's 90% interval excludes 0,
$|\hat\beta_k| - 1.645\,\text{se}_k > 0$; the card counts the hidden ones.

**Ranking.** Eligible cells: suitability at least the city median. Among them the forward test compared the 5th
percentile, the 20th percentile and the point estimate; with suitability as the score they were indistinguishable
(`ABLATION.md`), so the point estimate ranks (`opts["rank"] = "q"` restores the 20th percentile) and the 90% interval
is shown, not floored.

**Narrow concepts.** "Specialty coffee" ≠ every café. Presences are cafés whose name or brand carries a cue
(coffee, roast, espresso, barista, brew, specialty; chains such as Starbucks, % Arabica, Costa) or the Overture
`coffee_roastery` category, when at least 30 venues match; every café remains a Huff competitor with
attractiveness $A = 0.5$ ($A = 1$ for the narrow set). Otherwise the broad set is used and the output says so.

**Comparable across concepts (unit → concepts).** Raw scores come from different models. Concepts are ranked by
the unit's percentile within each concept's own city distribution of opportunity, then by a Platt-calibrated
probability (Platt 1999) $P(\text{cell hosts the concept}) = \sigma(a\,\eta^{\text{oof}} + b)$ fitted on the
out-of-fold logits of all study cells (Brier score and base rate reported). Concepts with fewer than 30 presences
are listed as "insufficient data", not ranked.

## 10. Break-even scenario model (`cityscan_breakeven.py`): a scenario, not a forecast

$$r_j = \frac{E_j}{\sum_i D_i / \hat N},\qquad
\text{covers}_j = \bar c\, r_j\, \varepsilon,\qquad
\text{rev} = \text{covers}\cdot t \cdot d,\qquad
\pi = \text{rev}\,(1 - c_m - c_v) - \text{rent} - S - F,$$

$r_j$ = captured demand of the site relative to an average existing competitor (Huff; $\hat N$ = competitors
corrected by the Chapman detection rate $q$, and the Huff competitor sums $S_i$ are scaled by $1/q$ for the same
reason, so the new venue competes with mapped *and* unmapped venues), $\bar c$ = covers/day of an average existing
venue, $\varepsilon$ = lognormal error of the demand index ($\sigma = 0.7$, a factor of 2 at one sd: stated, and
wide on purpose because the index did not predict openings in `ABLATION.md`), $t$ = ticket, $d$ = open days, $c_m, c_v$ = food and
variable cost shares, $S$ = staff, $F$ = other fixed costs; payback = capex / $\pi$. Triangular priors per city
and profile (`DEFAULTS`, each with its source or "assumption"), 2,000 draws with a fixed seed:
$P(\pi \ge 0)$, profit P10/P50/P90, median rent-to-revenue, median payback, $P(\text{payback} \le 36$ months$)$.

**Global sensitivity.** First-order Sobol indices by the Saltelli et al. (2010) estimator and total indices by
Jansen (1999), on monthly profit, with matrices $A, B$ ($n = 2048$; outputs centred, which keeps the estimators
unbiased and tames the variance caused by the heavy-tailed demand error) and $A_B^{(i)}$:

$$S_i = \frac{\tfrac1n\sum_j f(B)_j\,[f(A_B^{(i)})_j - f(A)_j]}{\operatorname{Var}(Y)},\qquad
S_{T,i} = \frac{\tfrac1{2n}\sum_j [f(A)_j - f(A_B^{(i)})_j]^2}{\operatorname{Var}(Y)},$$

plus one-at-a-time tornado data (each input at its P10 and P90, others at their medians). The page recomputes the
scenario when an input is edited (same formulas, browser RNG).

Since section 11 this scenario card is no longer shown on unit cards. Official-calibrated cities get the calibrated
survival card; other cities get the conditional card. `breakeven_card` stays for the API and tests.

## 11. Break-even anchored on real outcomes (`cityscan_calibrate.py`, `cityscan_calibration.json`)

The section 10 scenario did not discriminate: Maârif concepts got P(break-even) 77–96%. Sobol put 87% of the
variance on the unsourced covers/day guess. The free demand parameters are now fitted to **official business
survival**. Two definitions:

- "still open after 36 months" is the observable proxy for "broke even on average";
- "official-calibrated" means an official register exists and the map data pass the survival study's quality gate.
  Paris (INSEE SIRENE) qualifies; Casablanca does not.

**Publishing gate: chance-corrected recall against an official register** (`cityscan_groundtruth.py`).
Two-source agreement measures whether OSM and Overture agree. It does not measure whether they miss businesses
that both lack. Where an official register with coordinates exists, the gate measures that directly:

- recall $R$: the share of registered food businesses with an OSM or Overture food place within 50 m;
- placebo $P$: the same share after shifting every register point 300 m in a random direction (fixed seed), i.e.
  the matches expected from map density alone;
- $\kappa_R = (R - P)/(1 - P)$, publish if $\kappa_R \ge 0.70$.

| City | Register | $n$ | $R$ | $P$ | $\kappa_R$ | Two-source agreement | Published |
|---|---|---|---|---|---|---|---|
| Paris | INSEE SIRENE (restaurants, cafés, bars) | 31,827 | 0.952 | 0.700 | 0.841 | 0.556 | yes |
| Chicago | business licences, food classes | 4,410 | 0.923 | 0.338 | 0.884 | 0.333 | yes |
| Philadelphia | L&I commercial activity licences | 5,793 | 0.704 | 0.257 | 0.602 | 0.214 | no, data-thin |

Chicago would fail the two-source gate (0.33) yet has the best register recall, so agreement is a weak proxy for
completeness, and it stays only as the fallback where no register exists (Casablanca, 0.181). By class,
Philadelphia restaurants score 0.62 and fast food 0.59, so the shortfall is not one category.
Registers are read in `/tmp` and never committed. The pack stores only $n$, $R$, $P$, $\kappa_R$, the radius, the
shift and the register's name.

**Runway model.** Expected covers/day at a location are $\theta\, r^{\kappa}$, with $r$ the Huff relative demand of
section 10. A venue's own covers are $\theta\, r^{\kappa} e^{\sigma z}$, with $z \sim N(0,1)$. With margin per
daily cover $m = t\,d\,(1-c_m-c_v)$ and fixed costs $K = S + F$:

$$\pi = \theta r^{\kappa}e^{\sigma z} m - \text{rent} - K,\qquad
\text{open at month } \tau \iff \pi \ge -bK/\tau,$$

where the cash buffer is $b = 6$ months of fixed costs (assumption; $b = 3$ and $9$ reported). In calibration, rent
is $\rho \times$ the location's *expected* revenue ($\rho \sim$ tri(5%, 10%, 15%)). The venue's own shock $z$ does
not change its rent. No open dataset gives Paris commercial rents by arrondissement: OLAP covers housing only, and
broker figures are ranges, not open data. The $\rho$ range is cited instead:

- 8–12% typical for restaurants, 12–15% for premium sites (L'Hôtellerie-Restauration; Almo);
- 10–15% typical in Paris (ProRestauration ratios 2026);
- 5.1% aggregate (INSEE Ésane 2023, via Journal du CHR).

On the card, rent is the user's figure. Other inputs are the Paris profile triangles of section 10, with common
random numbers.

**Estimator (method of moments).** Units are SIRENE employer establishments opened from 2014 in Paris. The concept
groups are NAF 56.10A restaurant, 56.10C fast food and 56.30Z bar/café. Follow-up is censored at the OSM-history
end, exactly as in the survival study (20,557 units, all inside the pack's study cells). For each group:

1. Each unit's relative demand $r_i$ is read from the group's analysis (concept "restaurant", "fast food" or
   "cafe"). It is binned into 40 equal-count quantiles.
2. For a given $\sigma$ (and $\kappa$), $\log\theta$ is the exact weighted quantile that makes the model's mean
   survival to 36 months equal the Kaplan–Meier $\hat S(3)$.
3. $\sigma$ is bisected so the mean survival to 12 months equals $\hat S(1)$. $\sigma$ is capped where the share of
   venues that can never cover rent would exceed $1-\hat S(1)$.
4. $\kappa \in [0,1]$ is bisected so the model's 3-year gap between units above and below the median $r$ equals
   the observed KM gap. An observed gap $\le 0$ gives $\kappa = 0$.

90% intervals come from a cluster bootstrap over 500 m blocks (100 replicates, Efron & Tibshirani 1993). A test
recovers known $(\theta, \sigma)$ and $\kappa$ from simulated venue outcomes.

| Group (Paris, SIRENE) | n | $\hat S(1)$ | $\hat S(3)$ | $\theta$ covers/day (90%) | $\sigma$ (90%) | $\kappa$ (90%) |
|---|---|---|---|---|---|---|
| restaurant | 10,092 | 0.949 | 0.813 | 137 (134–147) | 0.50 (0.47–0.55) | 0 (0–0) |
| fast food | 8,342 | 0.946 | 0.789 | 178 (173–182) | 0.44 (0.42–0.47) | 0 (0–0.02) |
| bar / café | 2,123 | 0.961 | 0.834 | 261 (239–275) | 0.49 (0.42–0.51) | 0 (0–0) |

**How to read these numbers.**

- $\kappa = 0$ in every group. The 3-year survival of units in higher-demand cells is *lower*: restaurant −1.7 pp,
  fast food −0.7 pp, bar/café −5.1 pp.
- A Cox model with the location covariates gives HR 0.975 (0.952–0.998) per SD of $\log r$. The Huff index adds
  almost nothing to survival.
- So the calibrated card does not use location demand. It discriminates through the user's rent compared with the
  calibrated typical revenue, plus the Cox location term.
- $\theta$ is identified only relative to the cost profile and $b$. The Paris staff and fixed-cost triangles imply
  a typical venue with $\theta$ covers/day. For example, a typical bar/café turns over about
  $261 \times 6 \times 26 \approx$ €41k/month, so its typical rent is about €4.1k.
- With $b = 3$, $\sigma$ falls to 0.06–0.15: almost no idiosyncratic risk is needed. With $b = 9$, $\sigma$ rises
  to 0.78–0.85. The level of risk is assumption-bound; the matching of $\hat S(1)$ and $\hat S(3)$ is not.

**Unit card (official-calibrated city).**

$$P(\text{open at 3 y}) = \Big[\tfrac1N\sum_k \mathbf 1\{\theta r^{\kappa}e^{\sigma z_k} m_k \ge \text{rent} + K_k(1-b/36)\}\Big]^{HR_{loc}},$$

with $HR_{loc} = 0.932^{z_{same}}\cdot 0.917^{z_{poi}}$. These are the pooled city-stratified Cox HRs of the survival
study, per SD of log(1+count):

- same-group venues within 150 m;
- points of interest within 200 m.

$z$ is standardised over the official units' locations using pack counts. The group HR (fast food 1.16, bar 0.87)
is *not* applied, because calibration is already per group.

The card also shows:

- P(open at 1 y) and P(monthly break-even) ($\tau = 0$, no buffer);
- the covers/day needed to break even at the input modes;
- the line "City baseline 3-yr survival for <group>: X% (official SIRENE, n)".

The 90% interval propagates the bootstrap $(\theta, \sigma, \kappa)$ and the HR confidence intervals; it does not
include uncertainty from the cost inputs. A *higher / lower risk than city* badge appears only when the interval
excludes the city $\hat S(3)$.

**Validation (held-out, 3-year IPCW Brier; Graf et al. 1999).**

- Spatial: the 20 arrondissements are split west/east by centroid. Calibrate on one half, score the other, both
  folds.
- Forward: calibrate on 2014–2018 openings, score 2019+ openings.

The Cox term is refitted on the training half only, with covariates at the opening year from the OSM history (the
pooled model's definitions). This avoids survivor leakage from the 2026 snapshot.

| Fold | city baseline | model, Huff as is ($\kappa=1$) | calibrated model | model × Cox | Cox only |
|---|---|---|---|---|---|
| train west → test east (n=10,507) | **0.1581** | 0.1768 | 0.1581 | 0.1583 | 0.1583 |
| train east → test west (n=10,050) | **0.1543** | 0.1675 | 0.1543 | 0.1543 | 0.1543 |
| forward 2014–18 → 2019+ (n=11,623) | 0.1403 | 0.1578 | 0.1403 | **0.1389** | 0.1390 |

**It does not beat the baseline.**

- Taking the Huff demand at face value ($\kappa = 1$) is 8–15% *worse* than the city average in every fold. That is
  why $\kappa$ is estimated.
- Calibrated, the model *equals* the city average ($\kappa = 0$).
- The location Cox term ranks units a little (C 0.535–0.56 per group). It improves Brier by 1% on the forward test
  and by nothing spatially.
- By quintile, model × Cox goes from 0.765 to 0.853 predicted vs 0.776 to 0.847 observed (restaurant, west → east).
  The spread is real but small.
- On the forward fold, 2019+ cohorts survived better than 2014–18 (restaurant 0.852 vs 0.779 train). Every method is
  6–7 pp too pessimistic there, a period effect that no location model sees.

**Replication: Chicago (City of Chicago business licences).**

The same script (`cityscan_calibrate.py --city chicago`) runs on new retail-food and tavern licences issued 2014+.
Closure is a revoked or cancelled status, a lapsed licence, or an out-of-business inspection, as in the survival
study's loader. Of 11,577 openings, 7,592 fall in the pack's study cells. The differences from Paris:

- costs are in USD (`DEFAULTS[("chicago", ...)]`), with staff sized at the Chicago minimum wage;
- the rent share is a US triangle, 4–6–10% of sales. The mode is the National Restaurant Association's 2024 urban
  median occupancy cost of 6.0%;
- with no arrondissements, the spatial folds are 1.5 km longitude strips.

| Group (Chicago, licences) | n | $\hat S(1)$ | $\hat S(3)$ | $\theta$ covers/day (90%) | $\sigma$ (90%) | $\kappa$ (90%) |
|---|---|---|---|---|---|---|
| restaurant | 5,692 | 0.918 | 0.664 | 73 (73–76) | 0.43 (0.42–0.49) | 0 (0–0.03) |
| fast food | 1,237 | 0.891 | 0.573 | 114 (109–120) | 0.44 (0.41–0.48) | 0 (0–0.05) |
| bar / café | 663 | 0.920 | 0.707 | 157 (145–166) | 0.53 (0.46–0.62) | 0 (0–0) |

| Fold (3-year IPCW Brier) | city baseline | Huff as is ($\kappa=1$) | calibrated model | model × Cox | Cox only |
|---|---|---|---|---|---|
| train west → test east (n=4,777) | **0.2218** | 0.3118 | 0.2219 | 0.2233 | 0.2232 |
| train east → test west (n=2,815) | 0.2351 | 0.3125 | 0.2388 | 0.2376 | **0.2342** |
| forward 2014–18 → 2019+ (n=4,170) | **0.2193** | 0.2861 | 0.2194 | 0.2194 | 0.2193 |

What replicates and what does not:

- $\kappa = 0$ again in every group. Higher-demand cells survive slightly *less*: restaurant −1.6 pp, fast food
  −3.5 pp, bar/café −8.6 pp at 3 years. Cox HR per SD of $\log r$ is 1.02 (0.99–1.06). So the finding that measured
  location demand carries no survival signal holds on a second continent and a different kind of register.
- Taking Huff at face value is 30–41% worse than the city average, worse than in Paris.
- The location Cox term does not rank Chicago units out of sample (C 0.47–0.54 per group).
- Rule: the calibrator applies the location HR only if the held-out C of model × Cox exceeds 0.5 in every spatial
  fold and group. Paris passes (lowest 0.535); Chicago fails (lowest 0.471), so Chicago ceilings use $HR_{loc} = 1$.
  They are city-level figures per tenant type, not a location signal.
- A typical-demand Chicago restaurant can pay \$3,230/month (90%: \$2,860–4,090) at the city's 3-year survival
  rate; fast food \$2,790, bar/café \$1,700.
- Chicago 3-year survival (0.57–0.71) is far below Paris (0.79–0.83), so the target the ceiling protects is lower.

**Data-thin cities (Casablanca): conditional card.**

- No probability is shown.
- Break-even covers/day are $N^* = (\text{rent} + S + F) / (t\,d\,(1-c_m-c_v))$ at the input modes.
- The monthly result at the user's covers $X$ and ticket $Y$ is $X\,Y\,d\,(1-c_m-c_v) - \text{rent} - S - F$.
- The card shows the reason: OSM sees ~14% (restaurant) or ~17% (café) of venues (Chapman), and the survival-study
  quality gate fails on all four criteria.

## 12. Rent ceiling: the calibrated model inverted for rent

**Why rent.** Section 11 found $\kappa = 0$: measured location demand carries almost no survival signal in Paris.
That is what Ricardian rent theory predicts (Ricardo 1817): the surplus a better location produces goes to the
landowner. In Alonso's (1964) bid-rent model, each land use bids up to the rent at which it earns a normal return.
Competition between tenants then pushes the rent to the highest bid. Once location advantage is priced into rent,
survival net of rent no longer depends on location. The variable a founder controls is the rent they agree to.

**Founder view.** For a spot, a concept group and a target survival $s^*$:

- Each common-random-number draw $k$ ($N = 2000$, fixed seed) has a rent capacity
  $A_k = \theta r^{\kappa} e^{\sigma z_k} m_k - K_k(1 - b/36)$. This is the highest monthly rent at which that venue
  is still open at 36 months.
- $P(\text{open at 3 y} \mid \text{rent}) = \big[\#\{A_k \ge \text{rent}\}/N\big]^{HR_{loc}}$. This is non-increasing
  in rent.
- $R^* = \max\{\text{rent} : P \ge s^*\}$, found by bisection to €1. It equals the $\lceil N s^{*1/HR}\rceil$-th
  largest $A_k$, so common random numbers make $R^*$ move smoothly with the parameters.
- The default $s^*$ is the group's official city 3-year survival; 70%, 80% and 90% are also reported.
- The 90% interval re-solves $R^*$ for each of the 100 bootstrap $(\theta, \sigma, \kappa)$, with $HR_{loc}$ drawn
  from the HR confidence intervals.

The card shows:

- the curve $P(\text{open at 3 y} \mid \text{rent})$ with its 90% band;
- the city baseline as a horizontal line;
- the asking rent as a marker, with its gap to the city norm in points;
- €/m² per month and per year when a surface is entered.

Tests check that the curve is monotone, that $R^*$ is monotone in the target, that bisection matches the order
statistic, and that $R^* = \theta r m - K(1 - b/36)$ exactly when every input is deterministic.

**Landlord view.** For one unit, $R^*$ is computed for every tenant type: restaurant, fast food and bar/café, each
at its own official city survival, plus a common 80% target. Types are ranked by $R^*$. Catalog concepts are listed
under the group whose calibration they use.

| Group | Catalog concepts |
|---|---|
| restaurant | sushi, Moroccan fine dining, Lebanese shawarma, Italian trattoria, French bistro, Indian curry, tacos, vegan bowls |
| fast food | ice cream, Korean fried chicken, burger, pizza |
| bar/café | specialty coffee, tea house, bakery café, brunch |

With $\kappa = 0$, the ranking reflects each group's calibrated revenue relative to its cost structure. Location
moves it only through the Cox term (same-group density and POI within 150–200 m). In effect it is the citywide bid
rent of each tenant type, adjusted for the spot.

**Data-thin cities (Casablanca).** No survival target is used. The conditional ceiling is the rent at which the
user's covers $X$ and ticket $Y$ exactly break even: $X Y d (1 - c_m - c_v) - S - F$. The landlord list uses the
stated default covers and tickets, which are assumptions, and the page labels them as such.

**Capitalisation test (`cityscan_dvf.py`).** Data: DVF géolocalisées (Etalab, Licence Ouverte), Paris 2021–2025.

- Kept: sales ("Vente") of exactly one *local industriel, commercial ou assimilé* with no dwelling in the same
  transaction, surface ≥ 10 m², and €500–60,000/m².
- 10,468 sales qualify. Per 400 m pack cell with at least 3 sales, the median log price per m² is used (416 cells,
  median €8,182/m²).
- These prices are correlated with the City Scan measures of the same cell. 90% intervals come from a block
  bootstrap over 2 km blocks (1,000 replicates).

| Cell measure | Spearman ρ (90%) | Pearson on logs (90%) | Elasticity of €/m² |
|---|---|---|---|
| site demand $D$ (footfall generators, distance-decayed) | **0.42 (0.30–0.51)** | 0.38 (0.28–0.46) | 0.16 |
| Huff captured demand $r$ (break-even input) | 0.17 (−0.03–0.34) | 0.15 (−0.03–0.28) | 0.19 |
| restaurants and fast food within 200 m | 0.22 (0.09–0.33) | 0.21 (0.11–0.31) | 0.07 |

**What the test shows.**

- Footfall demand is priced in: no bootstrap replicate is at or below 0. This is direct evidence for
  capitalisation.
- Competitor-adjusted captured demand is only weakly priced, and its interval includes 0. Prices follow where
  people are more than where competition is thin, which is consistent with section 11's finding that captured
  demand does not predict survival.

**Caveats.**

- Sale prices of commercial premises (all trades, shops and offices together) are a proxy for rents. They also
  capitalise expected rent growth and interest rates.
- The correlation is across cells, not causal.
- DVF has no premises use type, and its surface is the built surface.

**Interpreting $R^*$.** $R^*$ is the highest rent compatible with city-normal survival *under the stated cost
profile*. It is not a market rent. If the asking rent is above $R^*$, then either the operator must beat the cost
profile (ticket, staff) or accept below-norm survival odds.

## Parameters (`cityscan.PARAMS`)

| Symbol | Value | Meaning |
|---|---|---|
| $\lambda$ | 400 m | walking distance decay |
| $\alpha, \beta$ | 1, 2 | Huff attractiveness and distance exponents |
| $d_\min$ | 50 m | Huff distance floor |
| $R$ | 2 km | Huff catchment |
| $r$ | 300 m | Ripley radius |
| $\ell_2$ | 1 | ridge penalty |
| | 15 | minimum presences to fit |
| | 5, 200, 100 | CV folds, bootstrap resamples, Monte Carlo draws |

## References

- Huff, D. L. (1963). A probabilistic analysis of shopping center trade areas. *Land Economics* 39(1), 81–90.
- Phillips, S. J., Anderson, R. P., Schapire, R. E. (2006). Maximum entropy modeling of species geographic
  distributions. *Ecological Modelling* 190, 231–259.
- Fithian, W., Hastie, T. (2013). Finite-sample equivalence in statistical models for presence-only data.
  *Annals of Applied Statistics* 7(4), 1917–1939.
- Roberts, D. R. et al. (2017). Cross-validation strategies for data with temporal, spatial, hierarchical, or
  phylogenetic structure. *Ecography* 40, 913–929.
- Moran, P. A. P. (1950). Notes on continuous stochastic phenomena. *Biometrika* 37, 17–23.
- Cliff, A. D., Ord, J. K. (1981). *Spatial Processes: Models and Applications*. Pion.
- Getis, A., Ord, J. K. (1992). The analysis of spatial association by use of distance statistics.
  *Geographical Analysis* 24(3), 189–206. Ord, J. K., Getis, A. (1995), *Geographical Analysis* 27(4), 286–306.
- Ripley, B. D. (1976). The second-order analysis of stationary point processes. *J. Applied Probability* 13, 255–266.
  Besag, J. (1977). Discussion of Ripley. *JRSS B* 39, 193–195.
- Casella, G. (1985). An introduction to empirical Bayes data analysis. *The American Statistician* 39(2), 83–87.
- Hanley, J. A., McNeil, B. J. (1982). The meaning and use of the area under a ROC curve. *Radiology* 143, 29–36.
- Wilson, E. B., Hilferty, M. M. (1931). The distribution of chi-square. *PNAS* 17, 684–688.
- Chapman, D. G. (1951). Some properties of the hypergeometric distribution with applications to zoological sample
  censuses. *University of California Publications in Statistics* 1, 131–160.
- Seber, G. A. F. (1982). *The Estimation of Animal Abundance and Related Parameters*, 2nd ed. Griffin.
- Ibrahim, J. G., Chen, M.-H. (2000). Power prior distributions for regression models. *Statistical Science* 15(1),
  46–60.
- Overture Maps Foundation, Places theme, release 2026-09-23.1, CDLA-Permissive-2.0.
- Platt, J. (1999). Probabilistic outputs for support vector machines and comparisons to regularized likelihood
  methods. In *Advances in Large Margin Classifiers*, MIT Press, 61–74.
- Saltelli, A., Annoni, P., Azzini, I., Campolongo, F., Ratto, M., Tarantola, S. (2010). Variance based sensitivity
  analysis of model output. Design and estimator for the total sensitivity index. *Computer Physics
  Communications* 181(2), 259–270.
- Jansen, M. J. W. (1999). Analysis of variance designs for model output. *Computer Physics Communications*
  117(1–2), 35–43.
- Efron, B., Tibshirani, R. J. (1993). *An Introduction to the Bootstrap*. Chapman & Hall (paired percentile
  bootstrap).
- Kaplan, E. L., Meier, P. (1958). Nonparametric estimation from incomplete observations. *JASA* 53, 457–481.
- Cox, D. R. (1972). Regression models and life-tables. *JRSS B* 34, 187–220.
- Graf, E., Schmoor, C., Sauerbrei, W., Schumacher, M. (1999). Assessment and comparison of prognostic
  classification schemes for survival data. *Statistics in Medicine* 18, 2529–2545 (IPCW Brier score).
- Hansen, L. P. (1982). Large sample properties of generalized method of moments estimators. *Econometrica* 50,
  1029–1054.
- Ricardo, D. (1817). *On the Principles of Political Economy and Taxation*, ch. 2 "On Rent". John Murray.
- Alonso, W. (1964). *Location and Land Use: Toward a General Theory of Land Rent*. Harvard University Press.
- DGFiP / Etalab, Demandes de valeurs foncières géolocalisées (DVF), Paris 2021–2025, Licence Ouverte 2.0.
- INSEE, base SIRENE des entreprises et de leurs établissements (Licence Ouverte 2.0), extraction 2026.
- Rent-to-revenue: L'Hôtellerie-Restauration (8–12%, 12–15% premium); ProRestauration, ratios de gestion 2026
  (10–15% typique Paris); Almo (8–12%); INSEE Ésane 2023 via Journal du CHR (5.1% aggregate).
