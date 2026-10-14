# Stable Street: taste-aware tenant mix with a stability certificate

Replaces the plain Gale–Shapley item in the darkhorse round.

## Model
- Units u on a street or in a mall, with adjacency (neighbours within ~100 m, or the same mall floor).
- Candidate tenants t (concepts).
- Stand-alone value v(t,u): the existing fit score, i.e. rent-first and the Qloo fit at that unit.
- Neighbour effect a(t,s): how much having s next door changes t's value.
  - Estimate it from Qloo audience overlap and affinity between the two concepts.
  - High overlap within the same category means cannibalisation (a < 0).
  - High overlap across complementary categories means shared footfall (a > 0).
  - a is asymmetric in general (a(t,s) ≠ a(s,t)).
  - The mapping from Qloo signals to a must be stated and labelled uncalibrated.
- Tenant t's value at u, given the assignment μ: U_t(u) = v(t,u) + Σ over occupied neighbours w of a(t, μ(w)).

## Results (each checked on the stated test set before writing this spec)
1. **Existence and convergence with negotiable rent.** If rent can be renegotiated (transferable utility), total street surplus S(μ) is an exact potential.
   - S only increases under two moves: an outsider replacing an occupant, or two occupants swapping units, in each case only when the whole affected group gains after compensation.
   - The assignment set is finite, so local search always stops at a stable assignment.
   - Classical argument: Monderer & Shapley 1996; Bogomolnaia & Jackson 2002.
2. **Only the symmetric part of taste effects matters when rent is negotiable.** In any pair's joint surplus, a(t,s) and a(s,t) always appear together, so the one-sided part cancels.
3. **Without rent negotiation, stability can fail.**
   - With asymmetric neighbour effects and fixed rents, 25 of 400 random 3-unit streets (≈6%) had no stable assignment at all: every assignment was blocked by a profitable swap or replacement.
   - With symmetric effects this happened in 0 of 400.
   - Product diagnostic: "This street needs rent flexibility to settle."
4. **Quality (empirical, 300 random 4-unit / 6-tenant streets).**
   - Stable assignments reached on average 96.4% of the best possible total surplus; the worst case was 72.4%.
   - 56% were exactly optimal.
   - Local search converged in at most 6 improving steps.
   - This is a simulation, not a proven bound.
5. **Certificate.** Anyone can verify stability in O(units × tenants + units²) by checking every replacement and swap. The UI shows "No improving move: N replacements and M swaps checked".

## Prior work (cite it; this is NOT a new theorem)
- **Existence theory:** Pycia & Yenmez, matching with externalities (UZH econ WP 392).
- **Mall tenant allocation by deferred acceptance with transfers:** see "Tenant allocation in shopping malls based on deferred acceptance algorithm".
- **Potential maximisation for retail agglomeration:** Osawa et al., arXiv 2011.06778.
- **Mall contracts internalising externalities:** Prendergast, Gould & Pashigian, CEPR DP3598.
- **Original contribution (claim only this):**
  - Estimating the neighbour-effect matrix from measured cultural-taste data (Qloo).
  - The asymmetry diagnostic in result 3.
  - A public, verifiable stability certificate in a leasing tool.
  - Pre-registering the outcome on the prediction ledger.

## Build constraints
- Portfolio of 3–5 units.
- Reuse per-run Qloo data; the total budget stays ≤ 25 calls and < 300 s.
- Unit tests:
  - The certificate holds on random instances.
  - The potential strictly increases each step.
  - A known 3-unit asymmetric counterexample is reported as "needs rent flexibility".
- Reference simulation: /tmp/stable_street.py (re-implement it in the repo's tests; don't import it).
