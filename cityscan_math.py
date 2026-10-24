"""Pure, standard-library maths behind City Scan. Formulas and citations are in METHOD.md.

Everything here is deterministic given its inputs (random draws take an explicit seed) and has no I/O.
"""
import math
import random

EARTH_R_M = 6_371_008.8
Z90 = 1.6448536269514722  # standard normal 95th percentile: two-sided 90% interval


# ---------------------------------------------------------------------------------------------------- basics

def sigmoid(z):
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def percentile(values, q):
    if not values:
        return 0.0
    v = sorted(values)
    k = (len(v) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def haversine_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * EARTH_R_M * math.asin(min(1.0, math.sqrt(a)))


class Projection:
    """Local equirectangular projection to metres (same constants as the City Scan grid); ~0.5% scale error."""

    def __init__(self, lat0, lon0):
        self.lat0, self.lon0 = lat0, lon0
        self.ky = 110_570.0
        self.kx = 111_320.0 * math.cos(math.radians(lat0))

    def xy(self, lat, lon):
        return (lon - self.lon0) * self.kx, (lat - self.lat0) * self.ky


def exp_kernel(d, lam=400.0):
    """Exponential distance decay K(d) = exp(-d / lambda)."""
    return math.exp(-max(d, 0.0) / lam)


# ---------------------------------------------------------------------------------------------------- spatial hash

class GridIndex:
    """Uniform spatial hash over projected points: radius queries touch only nearby buckets, O(N) to build."""

    def __init__(self, points, size_m):
        self.size = size_m
        self.buckets = {}
        for p in points:  # p = (x, y, payload)
            self.buckets.setdefault((math.floor(p[0] / size_m), math.floor(p[1] / size_m)), []).append(p)

    def within(self, x, y, r):
        bx, by, k = math.floor(x / self.size), math.floor(y / self.size), math.ceil(r / self.size)
        out = []
        for i in range(bx - k, bx + k + 1):
            for j in range(by - k, by + k + 1):
                for p in self.buckets.get((i, j), ()):
                    d = math.hypot(p[0] - x, p[1] - y)
                    if d <= r:
                        out.append((d, p))
        return out


def kernel_density_at(index, x, y, lam=400.0, cutoff=None):
    """D(x) = sum_g w_g K(d(x, g)) over indexed points (payload = weight), truncated at `cutoff` (default 3 lambda)."""
    return sum(p[2] * exp_kernel(d, lam) for d, p in index.within(x, y, cutoff or 3 * lam))


def cell_kernel_offsets(cell_m, lam=400.0, cutoff=None, sub=5):
    """Grid-cell kernel stencil: (dr, dc, E[K(d)]) for a point uniform in cell (r+dr, c+dc) seen from the centre of (r, c).

    The expectation over a sub x sub lattice inside the source cell avoids the bias of using centroid distance
    (notably K(0) = 1 for the cell itself).
    """
    cutoff = cutoff or 3 * lam
    reach = math.ceil(cutoff / cell_m)
    offs = []
    for dr in range(-reach, reach + 1):
        for dc in range(-reach, reach + 1):
            if math.hypot(max(abs(dr) - 0.5, 0), max(abs(dc) - 0.5, 0)) * cell_m > cutoff:
                continue
            acc = 0.0
            for a in range(sub):
                for b in range(sub):
                    dy = (dr + (a + 0.5) / sub - 0.5) * cell_m
                    dx = (dc + (b + 0.5) / sub - 0.5) * cell_m
                    acc += exp_kernel(math.hypot(dx, dy), lam)
            offs.append((dr, dc, acc / sub / sub))
    return offs


def smooth(values, rows, cols, offsets, only=None):
    """out_i = sum_j values_j E[K(d_ij)] with the stencil; O(cells x stencil). `only`: subset of target cells."""
    out = [0.0] * (rows * cols)
    targets = range(rows * cols) if only is None else only
    for i in targets:
        r, c = divmod(i, cols)
        s = 0.0
        for dr, dc, k in offsets:
            rr, cc = r + dr, c + dc
            if 0 <= rr < rows and 0 <= cc < cols:
                v = values[rr * cols + cc]
                if v:
                    s += v * k
        out[i] = s
    return out


# ---------------------------------------------------------------------------------------------------- Huff

def huff_probabilities(d_new, d_competitors, a_new=1.0, a_competitors=None, alpha=1.0, beta=2.0, floor_m=50.0):
    """Huff (1963): P_j = A_j^a d_j^-b / sum_all A^a d^-b. Returns (P_new, [P_k]); they sum to 1."""
    a_competitors = a_competitors or [1.0] * len(d_competitors)
    u_new = a_new ** alpha * max(d_new, floor_m) ** -beta
    u = [a ** alpha * max(d, floor_m) ** -beta for a, d in zip(a_competitors, d_competitors)]
    tot = u_new + sum(u)
    return u_new / tot, [x / tot for x in u]


def huff_share(d_new, competitor_utility, a_new=1.0, alpha=1.0, beta=2.0, floor_m=50.0):
    """P_ij given S_i = sum_k A_k^a d_ik^-b precomputed for the demand cell."""
    u = a_new ** alpha * max(d_new, floor_m) ** -beta
    return u / (u + competitor_utility)


# ---------------------------------------------------------------------------------------------------- Ripley

def local_l_excess(n_within_r, n_total, area_m2, r):
    """Local L-function excess at a location: L(r) - r, with K(r) = A n_r / n (no edge correction).

    0 under complete spatial randomness, > 0 when same-category venues cluster around the location.
    """
    if n_total <= 0 or area_m2 <= 0:
        return -r
    k = area_m2 * n_within_r / n_total
    return math.sqrt(k / math.pi) - r


# ---------------------------------------------------------------------------------------------------- logistic regression

def standardize(rows):
    """Column means and standard deviations (sd 1 for constant columns)."""
    p, n = len(rows[0]), len(rows)
    mu = [sum(r[k] for r in rows) / n for k in range(p)]
    sd = [math.sqrt(sum((r[k] - mu[k]) ** 2 for r in rows) / n) or 1.0 for k in range(p)]
    return mu, sd


def _solve(a, b):
    """Gaussian elimination with partial pivoting; a is modified."""
    n = len(b)
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(a[r][col]))
        a[col], a[piv], b[col], b[piv] = a[piv], a[col], b[piv], b[col]
        d = a[col][col] or 1e-12
        for r in range(col + 1, n):
            f = a[r][col] / d
            if f:
                for k in range(col, n):
                    a[r][k] -= f * a[col][k]
                b[r] -= f * b[col]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (b[r] - sum(a[r][k] * x[k] for k in range(r + 1, n))) / (a[r][r] or 1e-12)
    return x


def fit_logistic(X, y, weights=None, l2=1.0, max_iter=30, tol=1e-7, prior=None, fixed=(), beta0=None):
    """L2-penalised weighted logistic regression, intercept unpenalised.

    Minimises -sum_i w_i [y_i log p_i + (1 - y_i) log(1 - p_i)] + (l2 / 2) ||beta - prior||^2 by Newton-Raphson
    (iteratively reweighted least squares): the loss is convex, so this second-order gradient descent reaches the
    same optimum as first-order descent in ~10 iterations instead of thousands. `prior` (slopes, default 0) makes
    it the MAP estimate under a Gaussian prior N(prior, I / l2); slopes listed in `fixed` (0-based feature
    indices) are held at 0. Returns [b0, b1..bp].
    """
    n, p = len(X), len(X[0])
    w = weights or [1.0] * n
    m0 = [0.0] + list(prior or [0.0] * p)
    fixed = set(fixed)
    beta = list(beta0) if beta0 else [0.0] * (p + 1)
    for k in fixed:
        beta[k + 1] = 0.0
    for _ in range(max_iter):
        g = [0.0] * (p + 1)
        h = [[0.0] * (p + 1) for _ in range(p + 1)]
        for xi, yi, wi in zip(X, y, w):
            row = (1.0, *xi)
            mu = sigmoid(sum(b * v for b, v in zip(beta, row)))
            r, s = wi * (yi - mu), wi * mu * (1 - mu)
            for a in range(p + 1):
                g[a] += r * row[a]
                sa = s * row[a]
                ha = h[a]
                for b in range(a, p + 1):
                    ha[b] += sa * row[b]
        for a in range(p + 1):
            for b in range(a):
                h[a][b] = h[b][a]
        for a in range(1, p + 1):
            g[a] -= l2 * (beta[a] - m0[a])
            h[a][a] += l2
        for k in fixed:
            a = k + 1
            g[a] = 0.0
            for b in range(p + 1):
                h[a][b] = h[b][a] = 0.0
            h[a][a] = 1.0
        step = _solve(h, g)
        beta = [b + s for b, s in zip(beta, step)]
        if max(abs(s) for s in step) < tol:
            break
    return beta


def logistic_gradient(X, y, weights, beta, l2=1.0, prior=None):
    """Gradient of the penalised log-likelihood (the quantity fit_logistic sets to zero)."""
    p = len(beta)
    m0 = [0.0] + list(prior or [0.0] * (p - 1))
    g = [0.0] * p
    for xi, yi, wi in zip(X, y, weights):
        r = wi * (yi - sigmoid(logit(beta, xi)))
        g[0] += r
        for a in range(1, p):
            g[a] += r * xi[a - 1]
    for a in range(1, p):
        g[a] -= l2 * (beta[a] - m0[a])
    return g


def fit_logistic_nonneg(X, y, weights=None, l2=1.0, prior=None, nonneg=(), max_rounds=None):
    """MAP logistic regression with sign constraints beta_k >= 0 for k in `nonneg` (0-based feature indices).

    Active-set method: fit, pin the most negative constrained slope at 0, refit; release a pinned slope when the
    gradient at 0 points to a positive value (KKT). Returns (beta, pinned feature indices)."""
    w = weights or [1.0] * len(X)
    nonneg = set(nonneg)
    pinned, beta = set(), None
    for _ in range(max_rounds or 2 * len(nonneg) + 2):
        beta = fit_logistic(X, y, w, l2=l2, prior=prior, fixed=pinned, beta0=beta)
        bad = [k for k in nonneg - pinned if beta[k + 1] < 0]
        if bad:
            pinned.add(min(bad, key=lambda k: beta[k + 1]))
            continue
        if not pinned:
            break
        g = logistic_gradient(X, y, w, beta, l2, prior)
        release = [k for k in pinned if g[k + 1] > 1e-6]
        if not release:
            break
        pinned.discard(max(release, key=lambda k: g[k + 1]))
    return beta, pinned


def logit(beta, x):
    return beta[0] + sum(b * v for b, v in zip(beta[1:], x))


def logistic_covariance(X, weights, beta, l2=1.0, fixed=()):
    """Laplace approximation: inverse Hessian of the penalised loss at the optimum (coefficient covariance).
    Slopes in `fixed` (pinned at a constraint boundary) get zero variance."""
    p = len(beta)
    h = [[0.0] * p for _ in range(p)]
    for xi, wi in zip(X, weights):
        row = (1.0, *xi)
        mu = sigmoid(logit(beta, xi))
        s = wi * mu * (1 - mu)
        for a in range(p):
            for b in range(p):
                h[a][b] += s * row[a] * row[b]
    for a in range(1, p):
        h[a][a] += l2
    pin = {k + 1 for k in fixed}
    for a in pin:
        for b in range(p):
            h[a][b] = h[b][a] = 0.0
        h[a][a] = 1.0
    cov = invert(h)
    for a in pin:
        for b in range(p):
            cov[a][b] = cov[b][a] = 0.0
    return cov


def invert(m):
    n = len(m)
    cols = [_solve([row[:] for row in m], [1.0 if r == c else 0.0 for r in range(n)]) for c in range(n)]
    return [[cols[c][r] for c in range(n)] for r in range(n)]


def cholesky(a):
    """Lower-triangular L with L L^T = a (a symmetric positive semi-definite; tiny negatives clipped)."""
    n = len(a)
    low = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            s = a[i][j] - sum(low[i][k] * low[j][k] for k in range(j))
            low[i][j] = math.sqrt(max(s, 0.0)) if i == j else (s / low[j][j] if low[j][j] > 0 else 0.0)
    return low


def mvn_draw(mean, chol, rng):
    eps = [rng.gauss(0.0, 1.0) for _ in mean]
    return [m + sum(chol[i][k] * eps[k] for k in range(i + 1)) for i, m in enumerate(mean)]


# ---------------------------------------------------------------------------------------------------- validation

def auc(pos, neg):
    """Area under the ROC curve = Mann-Whitney U / (n1 n0), ties counted half."""
    if not pos or not neg:
        return float("nan")
    allv = sorted([(v, 1) for v in pos] + [(v, 0) for v in neg])
    rank_sum, i = 0.0, 0
    while i < len(allv):
        j = i
        while j + 1 < len(allv) and allv[j + 1][0] == allv[i][0]:
            j += 1
        avg = (i + j) / 2 + 1
        rank_sum += avg * sum(1 for k in range(i, j + 1) if allv[k][1])
        i = j + 1
    n1, n0 = len(pos), len(neg)
    return (rank_sum - n1 * (n1 + 1) / 2) / (n1 * n0)


def bootstrap_auc_ci(pos, neg, n_boot=200, seed=7, level=0.95):
    """Percentile bootstrap CI, resampling presences and background separately (stratified)."""
    rng = random.Random(seed)
    stats = []
    for _ in range(n_boot):
        p = [pos[rng.randrange(len(pos))] for _ in pos]
        q = [neg[rng.randrange(len(neg))] for _ in neg]
        stats.append(auc(p, q))
    a = (1 - level) / 2
    return percentile(stats, a), percentile(stats, 1 - a)


def block_folds(coord, k=5):
    """Spatial block folds: k contiguous bands of equal size along one coordinate (Roberts et al. 2017)."""
    order = sorted(set(coord))
    cuts = [percentile(order, q / k) for q in range(1, k)]
    return [sum(1 for c in cuts if v > c) for v in coord]


def auc_scores(y, s):
    """AUC of scores s for cells with y > 0 against cells with y == 0."""
    return auc([v for t, v in zip(y, s) if t > 0], [v for t, v in zip(y, s) if t <= 0])


def top_share(y, s, frac=0.2, k=None):
    """Share of the total of y (e.g. venues per cell) falling in the top `frac` (or top k) cells by score s.
    Ties at the cut-off are shared pro rata, so a constant score gives exactly `frac` in expectation."""
    n = len(s)
    k = max(1, math.ceil(frac * n)) if k is None else min(k, n)
    tot = sum(y)
    if tot <= 0:
        return float("nan")
    cut = sorted(s, reverse=True)[k - 1]
    above = [t for t, v in zip(y, s) if v > cut]
    tied = [t for t, v in zip(y, s) if v == cut]
    slots = k - len(above)
    return (sum(above) + (sum(tied) * slots / len(tied) if tied else 0.0)) / tot


def paired_bootstrap_diff(y, s_a, s_b, metric, n_boot=200, seed=7, level=0.95):
    """metric(y, s_a) - metric(y, s_b) with a percentile bootstrap CI; cells resampled with replacement,
    stratified by y > 0, the same resample used for both scores (paired)."""
    pos = [i for i, t in enumerate(y) if t > 0]
    neg = [i for i, t in enumerate(y) if t <= 0]
    point = metric(y, s_a) - metric(y, s_b)
    rng = random.Random(seed)
    stats = []
    for _ in range(n_boot):
        idx = [pos[rng.randrange(len(pos))] for _ in pos] + [neg[rng.randrange(len(neg))] for _ in neg]
        yy = [y[i] for i in idx]
        stats.append(metric(yy, [s_a[i] for i in idx]) - metric(yy, [s_b[i] for i in idx]))
    a = (1 - level) / 2
    return point, percentile(stats, a), percentile(stats, 1 - a)


def platt(scores, y):
    """Platt (1999) scaling: P(y = 1 | s) = sigmoid(a s + b), fitted by (unpenalised) logistic regression."""
    b0, a = fit_logistic([[s] for s in scores], [1 if t > 0 else 0 for t in y], l2=1e-6)
    return a, b0


def brier(y, p):
    return sum(((1 if t > 0 else 0) - q) ** 2 for t, q in zip(y, p)) / len(y)


# ---------------------------------------------------------------------------------------------------- sensitivity

def sobol_indices(f, samplers, n=1024, seed=7):
    """First-order (Saltelli et al. 2010) and total-order (Jansen 1999) Sobol indices of f(x) for independent
    inputs x_i ~ samplers[i](rng). Cost (k + 2) n evaluations. Returns (S1, ST, variance)."""
    rng = random.Random(seed)
    k = len(samplers)
    A = [[s(rng) for s in samplers] for _ in range(n)]
    B = [[s(rng) for s in samplers] for _ in range(n)]
    fa, fb = [f(x) for x in A], [f(x) for x in B]
    allv = fa + fb
    mean = sum(allv) / len(allv)
    var = sum((v - mean) ** 2 for v in allv) / len(allv)
    # Centring the outputs leaves both estimators unbiased and cuts the variance of S1 when |mean| >> sd.
    fa, fb = [v - mean for v in fa], [v - mean for v in fb]
    s1, st = [], []
    for i in range(k):
        fab = [f(a[:i] + [b[i]] + a[i + 1:]) - mean for a, b in zip(A, B)]
        if var <= 0:
            s1.append(0.0)
            st.append(0.0)
            continue
        s1.append(sum(vb * (vab - va) for va, vb, vab in zip(fa, fb, fab)) / n / var)
        st.append(sum((va - vab) ** 2 for va, vab in zip(fa, fab)) / (2 * n) / var)
    return s1, st, var


# ---------------------------------------------------------------------------------------------------- empirical Bayes

def eb_gamma_prior(counts, q=1.0):
    """Gamma(a, b) prior on true Poisson rates by the method of moments, with detection probability q.

    Observed n ~ Poisson(q lambda), lambda ~ Gamma(a, b): E[n] = q a/b, Var[n] = q a/b + q^2 a/b^2.
    With no overdispersion every cell shares the city rate (b -> large).
    """
    n = len(counts)
    if not n:
        return 0.0, 1.0
    m = sum(counts) / n
    v = sum((c - m) ** 2 for c in counts) / n
    if m <= 0:
        return 0.0, 1.0
    mean_l = m / q
    var_l = (v - m) / (q * q)
    b = mean_l / var_l if var_l > 0 else 1e6
    return mean_l * b, b


def gamma_posterior(a, b, count, q=1.0, kappa=1.0):
    """Posterior of the true rate for one cell: Gamma(kappa (a + n), kappa (b + q)) as (shape, rate).

    q: detection probability (exposure); kappa in (0, 1]: power-posterior discount for an incompletely mapped cell
    (same mean, variance inflated by 1 / kappa).
    """
    return kappa * (a + count), kappa * (b + q)


def gamma_interval(shape, rate, z=Z90):
    """Wilson-Hilferty approximation of the central Gamma interval (90% by default)."""
    if shape <= 0:
        return 0.0, 0.0
    c = 1 / (9 * shape)
    lo = shape / rate * max(0.0, 1 - c - z * math.sqrt(c)) ** 3
    hi = shape / rate * (1 - c + z * math.sqrt(c)) ** 3
    return lo, hi


# ---------------------------------------------------------------------------------------------------- two-source coverage

def coverage_gap(n_a, n_b):
    """Share of source b's count that source a misses: 1 - min(1, n_a / n_b) (0 when b has nothing)."""
    return 1.0 - min(1.0, n_a / n_b) if n_b > 0 else 0.0


def agreement(n_a, n_b):
    """min / max of two counts (None when both are 0)."""
    hi = max(n_a, n_b)
    return min(n_a, n_b) / hi if hi > 0 else None


def completeness(pairs):
    """Cell completeness: mean agreement over categories where either source has something (None if none)."""
    vals = [r for r in (agreement(a, b) for a, b in pairs) if r is not None]
    return sum(vals) / len(vals) if vals else None


def chapman(n1, n2, m):
    """Chapman (1951) bias-corrected Lincoln-Petersen estimate of the true total from two sources.

    N = (n1 + 1)(n2 + 1) / (m + 1) - 1, variance (Seber 1970) (n1+1)(n2+1)(n1-m)(n2-m) / ((m+1)^2 (m+2)).
    Returns dict(N, sd, ci95, union, missed_by_both).
    """
    n_hat = (n1 + 1) * (n2 + 1) / (m + 1) - 1
    var = (n1 + 1) * (n2 + 1) * (n1 - m) * (n2 - m) / ((m + 1) ** 2 * (m + 2))
    union = n1 + n2 - m
    sd = math.sqrt(max(var, 0.0))
    return {"N": n_hat, "sd": sd, "ci95": (max(union, n_hat - 1.96 * sd), n_hat + 1.96 * sd), "union": union,
            "missed_by_both": max(0.0, n_hat - union)}


def name_similarity(a, b):
    """0..1 similarity of two normalised names: max of character-sequence ratio, token Jaccard and token
    containment (0.9 when every token of the shorter name, all >= 3 characters long, appears in the longer one,
    e.g. a bilingual 'nafoura النافورة' vs 'nafoura')."""
    from difflib import SequenceMatcher

    if not a or not b:
        return 0.0
    ta, tb = set(a.split()), set(b.split())
    jac = len(ta & tb) / len(ta | tb) if ta | tb else 0.0
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    contained = 0.9 if short and short <= long_ and all(len(t) >= 3 for t in short) else 0.0
    return max(SequenceMatcher(None, a, b).ratio(), jac, contained)


def match_points(a, b, max_d=50.0, min_sim=0.6, unnamed_d=20.0):
    """Greedy one-to-one matching of two point sets [(x, y, normalised_name)].

    A pair matches when closer than max_d and names are similar (>= min_sim); when either name is missing the pair
    must be closer than unnamed_d. Best pairs first (similarity, then distance). Returns [(index_a, index_b)].
    """
    idx = GridIndex([(x, y, k) for k, (x, y, _) in enumerate(a)], max(max_d, 1.0))
    cands = []
    for kb, (x, y, nb) in enumerate(b):
        for d, p in idx.within(x, y, max_d):
            na = a[p[2]][2]
            if na and nb:
                s = name_similarity(na, nb)
                if s >= min_sim:
                    cands.append((-s, d, p[2], kb))
            elif d <= unnamed_d:
                cands.append((0.0, d, p[2], kb))
    cands.sort()
    used_a, used_b, out = set(), set(), []
    for _, _, ka, kb in cands:
        if ka not in used_a and kb not in used_b:
            used_a.add(ka)
            used_b.add(kb)
            out.append((ka, kb))
    return out


# ---------------------------------------------------------------------------------------------------- spatial autocorrelation

def rook_neighbors(rows, cols, keep=None):
    keep = set(range(rows * cols)) if keep is None else set(keep)
    out = {}
    for i in keep:
        r, c = divmod(i, cols)
        out[i] = [j for j in ((r - 1) * cols + c if r else -1, (r + 1) * cols + c if r + 1 < rows else -1,
                              i - 1 if c else -1, i + 1 if c + 1 < cols else -1) if j in keep]
    return out


def queen_neighbors(rows, cols, keep=None, self_=False):
    keep = set(range(rows * cols)) if keep is None else set(keep)
    out = {}
    for i in keep:
        r, c = divmod(i, cols)
        nb = [(r + dr) * cols + c + dc for dr in (-1, 0, 1) for dc in (-1, 0, 1)
              if (self_ or dr or dc) and 0 <= r + dr < rows and 0 <= c + dc < cols]
        out[i] = [j for j in nb if j in keep]
    return out


def morans_i(values, nbrs):
    """Global Moran's I with binary symmetric weights; z-score under the randomisation assumption (Cliff & Ord).

    values: {cell: x}; nbrs: {cell: [cells]}. Returns dict(I, expected, variance, z).
    """
    ids = list(values)
    n = len(ids)
    if n < 4:
        return {"I": float("nan"), "expected": float("nan"), "variance": float("nan"), "z": float("nan")}
    m = sum(values.values()) / n
    z = {i: values[i] - m for i in ids}
    s0 = sum(len(nbrs.get(i, ())) for i in ids)
    m2 = sum(v * v for v in z.values())
    if s0 == 0 or m2 == 0:
        return {"I": 0.0, "expected": -1 / (n - 1), "variance": 0.0, "z": 0.0}
    num = sum(z[i] * sum(z[j] for j in nbrs.get(i, ())) for i in ids)
    i_stat = n / s0 * num / m2
    s1 = 2.0 * s0
    s2 = sum((2.0 * len(nbrs.get(i, ()))) ** 2 for i in ids)
    b2 = n * sum(v ** 4 for v in z.values()) / m2 ** 2
    e = -1.0 / (n - 1)
    var = ((n * ((n * n - 3 * n + 3) * s1 - n * s2 + 3 * s0 * s0)
            - b2 * ((n * n - n) * s1 - 2 * n * s2 + 6 * s0 * s0))
           / ((n - 1) * (n - 2) * (n - 3) * s0 * s0)) - e * e
    return {"I": i_stat, "expected": e, "variance": var, "z": (i_stat - e) / math.sqrt(var) if var > 0 else 0.0}


def getis_ord_gi_star(values, nbrs_with_self):
    """Getis-Ord Gi* z-scores with binary weights that include the cell itself. values: {cell: x}."""
    ids = list(values)
    n = len(ids)
    xbar = sum(values.values()) / n
    s = math.sqrt(max(sum(v * v for v in values.values()) / n - xbar ** 2, 0.0))
    out = {}
    for i in ids:
        nb = [j for j in nbrs_with_self.get(i, ()) if j in values]
        w = len(nb)
        den = s * math.sqrt(max((n * w - w * w) / (n - 1), 0.0))
        out[i] = (sum(values[j] for j in nb) - xbar * w) / den if den > 0 else 0.0
    return out
