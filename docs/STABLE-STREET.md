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

## Results
The existence argument is prior work. The counts 25 of 400 and 96.4% below come from an external simulation that is not in this repository. This build's tests use their own generator and do not reproduce those two figures.
1. **Existence and convergence with negotiable rent.** If rent can be renegotiated (transferable utility), total street surplus S(μ) is an exact potential.
   - S only increases under two moves: an outsider replacing an occupant, or two occupants swapping units, in each case only when the whole affected group gains after compensation.
   - The assignment set is finite, so local search always stops at a stable assignment.
   - Classical argument: Monderer & Shapley 1996; Bogomolnaia & Jackson 2002.
2. **Only the symmetric part of taste effects matters when rent is negotiable.** In any pair's joint surplus, a(t,s) and a(s,t) always appear together, so the one-sided part cancels.
3. **Without rent negotiation, stability can fail.**
   - An external simulation, not reproduced by this repository, reported that with asymmetric neighbour effects and fixed rents, 25 of 400 random 3-unit streets (≈6%) had no stable assignment: every assignment was blocked by a profitable swap or replacement.
   - That same external simulation reported 0 of 400 when the effects were symmetric.
   - Product diagnostic: "This street needs rent flexibility to settle."
4. **Quality (empirical, 300 random 4-unit / 6-tenant streets).**
   - An external simulation, not reproduced by this repository, reported stable assignments at an average of 96.4% of the best possible total surplus, a worst case of 72.4%, 56% exactly optimal, and at most 6 improving steps.
   - This is a simulation, not a proven bound, and it is not a measurement from this build.
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
