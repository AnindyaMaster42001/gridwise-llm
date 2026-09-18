"""
VERIFIED SPIKE — the LP formulation from docs/03-optimizer-formulation.md.

Run it from the repo root:   python3 docs/reference/lp_spike.py

It solves every public sample case using that case's *expected* directives and
prints our cost next to the organizer's reference cost. All ten match at +0.00,
which is why docs/03 says to implement this formulation rather than a heuristic.

This file is reference material, not production code: app/optimizer.py is the
implementation, and it reads bounds off a ConstraintSet instead of re-deriving
them from raw directives here.
"""

import json, pathlib
import numpy as np
from scipy.optimize import linprog

P = pathlib.Path(__file__).resolve().parents[2] / 'tests' / 'data' / 'public_samples.json'
d = json.load(open(P))

def solve(inp, directives):
    H=24
    hours=sorted(inp['hours'], key=lambda x:x['hour'])
    dem=np.array([h['demand_kwh'] for h in hours],float)
    sol=np.array([h['solar_kwh'] for h in hours],float)
    tar=np.array([h['tariff_bdt_per_kwh'] for h in hours],float)
    b=inp['battery']
    cap=b['capacity_kwh']; E0=b['initial_energy_kwh']; base_min=b['minimum_energy_kwh']
    mc=b['max_charge_kwh_per_hour']; md=b['max_discharge_kwh_per_hour']
    eff=sol.copy()
    minres=np.full(H, base_min, float)
    gcap=np.full(H, np.inf)
    nocharge=set(); nodis=set()
    for dv in directives:
        t=dv['directive_type']; a=dv.get('structured_adjustment')
        if t=='solar_reduction':
            for h in a['hours']: eff[h]*=a['factor']
        elif t=='minimum_battery_reserve':
            for h in a['hours']: minres[h]=max(minres[h], a['minimum_energy_kwh'])
        elif t=='no_charge_window': nocharge|=set(a['hours'])
        elif t=='no_discharge_window': nodis|=set(a['hours'])
        elif t=='max_grid_window':
            for h in a['hours']: gcap[h]=min(gcap[h], a['max_grid_kwh'])
    # vars: g[0..23], s[24..47], c[48..71], dch[72..95]
    n=4*H
    cost=np.zeros(n); cost[:H]=tar
    Aeq=np.zeros((H+1,n)); beq=np.zeros(H+1)
    for h in range(H):
        Aeq[h,h]=1; Aeq[h,H+h]=1; Aeq[h,3*H+h]=1; Aeq[h,2*H+h]=-1; beq[h]=dem[h]
    Aeq[H,2*H:3*H]=1; Aeq[H,3*H:4*H]=-1; beq[H]=0.0  # end neutrality
    Aub=[]; bub=[]
    for h in range(H):
        row=np.zeros(n); row[2*H:2*H+h+1]=1; row[3*H:3*H+h+1]=-1
        Aub.append(row.copy()); bub.append(cap-E0)            # E<=cap
        Aub.append(-row); bub.append(E0-minres[h])            # E>=minres
    bounds=[]
    for h in range(H): bounds.append((0, gcap[h] if np.isfinite(gcap[h]) else None))
    for h in range(H): bounds.append((0, eff[h]))
    for h in range(H): bounds.append((0, 0 if h in nocharge else mc))
    for h in range(H): bounds.append((0, 0 if h in nodis else md))
    r=linprog(cost, A_ub=np.array(Aub), b_ub=np.array(bub), A_eq=Aeq, b_eq=beq, bounds=bounds, method='highs')
    return r

for c in d['cases']:
    dirs=[e for e in c['expected_output']['directive_interpretation'] if e['applies']]
    r=solve(c['input'], dirs)
    exp=c['expected_output']['total_cost_bdt']
    print(f"{c['id']}: status={r.status} ours={r.fun:.2f} expected={exp} diff={r.fun-exp:+.2f}")
