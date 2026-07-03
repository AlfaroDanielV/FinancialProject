# Phase 10 · B6 Retirement Engine — Costa Rica Pension Constants (source-verified)

> **Status:** 🟢 RESEARCH CLOSED — adversarially verified 2026-07-03 (two parallel research runs, each finding cross-checked against a second primary source). Companion to `docs/advisory-mode-and-principle-library.md` §5/B6.
> **Purpose:** the versioned, sourced constants to encode `app/domain/retirement/cr_pension.py`, with every number tagged **current-law vs proposal vs operator-methodology** and a confidence level. Body English; CR legal terms + quotes kept in Spanish.
> **Git:** root `.gitignore` blocks `docs/**` → commit with `git add -f`.

---

## 0. The finding that reorders B6

The original brief assumed two things exist as official constants to copy: "the IVM replacement ratio" and "the pesimista/esperado/optimista return triad." **Neither exists in that form.**

1. **The IVM replacement ratio is not a number — it is a decreasing bracket table** (52.5% → 43% of the reference salary; lower earners get more).
2. **SUPEN does not set the ROP scenario rates.** The Acuerdo **SP-A-243-2021** *mandates* the 3-scenario projection and its statement content, but **each operadora (OPC) derives its own triad from its own historical return series and republishes it every January.** So there is no national triad to copy.

**Consequence for the engine:** IVM is a deterministic *formula* (encode the reglamento); ROP is a *versioned, per-OPC × per-year methodology* (encode a reference OPC's current triad + the method, and re-verify each January). Do **not** hardcode a single ROP triad.

> ⚠️ **Correction to prior working numbers.** The `Optimista 8,80% / Esperado 8,00% / Pesimista 7,20%` + density `91,67/70,85/50%` figures are from **Popular Pensiones' April-2024 methodology** and are superseded twice over (ene-2025: 5,18/8,59/11,77; **ene-2026: 4,06/8,82/13,22, density by percentiles**). Do not encode the 2024 numbers.

---

## 1. IVM (CCSS basic regime) — CURRENT LAW, encodable today

Reform approved in CCSS Board session **9229 (14-dic-2021)**, in force since **11-ene-2024**. Primary anchor: Reglamento del Seguro de IVM (arts. 5, 23, 24, 25, 28, 29); corroborated by the CICR reform analysis and the pre-reform reglamento text (CEPAL/OIG).

### 1.1 Salario de referencia (SPR) — art. 23 · confidence HIGH
Average of the **best 300** monthly salaries (25 years) of the **whole career**, each **inflation-adjusted with INEC's IPC before selection** (not the 300 salaries immediately before retirement).
- Verbatim: *"el promedio de los mejores 300 (trescientos) salarios o ingresos mensuales… seleccionados posterior a su actualización por inflación… el IPC calculado mensualmente por el INEC."*
- Pre-reform (historical, do not use): last 240 salaries (20 years), placement by last 60 months.
- ⚠️ **Transition caveat:** the reform phases the salary count up over the transition window — a person retiring mid-transition may use fewer than 300. Encode a transition table if you model the transition cohort; otherwise 300 is the steady-state rule.

### 1.2 Cuantía básica (replacement bracket table) — art. 24 · confidence HIGH
Bracket = `SPR ÷ (salario mínimo de un trabajador de ocupación no calificada, Decreto de Salarios MTSS)`. The divisor changes yearly (ej. 2022 = ₡326.253,57; **2026 value = OPEN**, see §7).

| Salarios mínimos (SPR / salmín) | Cuantía básica |
|---|---|
| < 2 | **52,5%** |
| 2 – < 3 | 51,0% |
| 3 – < 4 | 49,4% |
| 4 – < 5 | 47,8% |
| 5 – < 6 | 46,2% |
| 6 – < 8 | 44,6% |
| ≥ 8 | **43,0%** |

The reform did **not** change these percentages; it changed the *salary* that feeds the table (now the SPR/best-300). Both extremes verified verbatim in two independent copies of the reglamento.

### 1.3 Cuantía adicional — art. 24 · confidence HIGH (threshold MEDIUM)
`+0,0833% of the SPR per month contributed in excess of 300 cuotas` (≈ **+1% per year over 25 years**).
- ⚠️ **240 vs 300 threshold:** post-reform sources say the "premio" covers the first **300** cuotas and the additional applies to the excess over 300; some secondary sources still say 240 (pre-reform). Confirm against the consolidated art. 24 (§7).

### 1.4 Postergación (deferral bonus) — art. 25 · confidence MEDIUM · ⚠️ BASE CORRECTED
`+0,1333% per month deferred past eligibility` (≈ **+1,6%/year**), applied on the **SALARIO PROMEDIO de referencia (art. 23)** — **NOT** on the ordinary pension (art. 24).
- Verbatim: *"pensión adicional consistirá en el 0,1333% por mes sobre el salario promedio calculado según el artículo 23º."*
- **This is a material correction:** 0,1333% of the reference salary ≠ 0,1333% of the ordinary pension (which is only 43–52,5% of that salary).
- Cap: ordinary + additional **≤ 125% of the reference salary** (MEDIUM — one reglamento version says "doble de la pensión ordinaria"; confirm in §7).
- ⚠️ **Reject the `1,5%/2%/2,5% per quarter` scheme** — it is **not** current law and could not be attested even as a documented proposal with those figures. Law = 0,1333%/month.

### 1.5 Pensión proporcional — art. 24 · confidence HIGH
Age 65 with **180 ≤ cuotas < 300** → `ordinary pension × (cuotas / 300)`.

### 1.6 Retirement requirements · confidence HIGH
- Ordinary old-age: **65 years + ≥300 cuotas (25 yrs)**, same age both sexes (since 12-ene-2024).
- Early retirement: **men eliminated**; women by table — `63a00 → 405 cuotas · 64a00 → 357 · 64a11 → 305 · ≥65 → 300`.
- (Síndrome de Down: age 40 — special provision.)

### 1.7 Operational parameters 2026
| Parameter | Value | Status / confidence |
|---|---|---|
| Cotización IVM total | **11,66%** desde 1-ene-2026 (patrono 5,58% / trabajador 4,33% / Estado 1,75%); prev. 11,16% | current_law / HIGH (official CCSS) |
| Next step | **12,16%** desde 1-ene-2029 (vigente 11,66% hasta 31-dic-2028) | current_law / HIGH |
| Pensión mínima IVM | ₡159.692 (dic-2025/ene-2026) → **₡162.295** desde feb-2026 (+1,63%) | current_law / HIGH (oficio GP-0607-2026) |
| Piso regulatorio de la mínima | **> 50% de la BMC** (art. 29) | current_law / MEDIUM |
| Pensión máxima IVM (ordinaria, sin postergación) | **≈ ₡1.666.062** (art. 28) — **NOT ₡2.500.000** (refuted: that was a "con postergación / 125%" scenario or calculator artifact) | current_law / MEDIUM — confirm 2026 value |
| BMC-IVM | **CONFLICT** — ₡324.590 (Decreto 45303-MTSS, La Gaceta 229, 5-dic-2025; with ₡311.990 = 2025 via Decreto 44756-MTSS) **vs** ₡311.990 cited as 2026 by a SUPEN page. Two-decree series favors **₡324.590**. Not load-bearing for a projection. | current_law / LOW — confirm |

---

## 2. ROP (Régimen Obligatorio de Pensiones Complementarias) — operator methodology, VERSIONED

Aporte total al ROP = **4,25% del salario** (Ley 7983), of which **1% is the worker's** (3,25% patrono). Retirement age used by every OPC to project = **65**.

**SUPEN does not publish a triad.** SP-A-243-2021 mandates a 3-scenario projection in the statement, built from **percentiles of the fund's own SUPEN-published historical returns**, projecting the balance to 65 and dividing by the **VANU (Valor Actuarial Necesario Unitario, by sex)**; SP-A-141-2010 governs desacumulación (Retiro Programado / Renta Permanente / Renta Temporal). All returns are **NOMINAL, per currency** (separate ₡ and $ series); the final pension is expressed in **present value** because the nominal balance is discounted by inflation.

### 2.1 Popular Pensiones — the reference (publishes fixed numbers) · confidence HIGH
**Manual de Metodología de Proyecciones ENERO 2026 (vigente):**
- **ROP scenarios (annual nominal, CRC, net of fees): Pesimista 4,06% · Esperado 8,82% · Optimista 13,22%.**
- Density by **percentiles** (not fixed): pesimista P35(H)/P30(M), optimista P45(both). Curve averages: pes H0,47/M0,4857 · esp H0,5978/M0,6383 · opt H0,6817/M0,75.
- Assumptions: **inflación 3%**, comisión sobre saldo ROP **0,35%/año**, edad ROP 65 / RVP 57, nominal balance discounted by inflation to present value.
- RVP ene-2026 (reference): A col 7,31/8,34/9,33 · B col 7,29/8,39/9,48 · A US$ 4,90/6,25/7,65 · B US$ 4,48/5,89/7,25.
- **Version trail (do not use the old ones):** abril-2024 = 8,80/8,00/7,20 + density 91,67/70,85/50; ene-2025 = 5,18/8,59/11,77. **Both superseded by ene-2026.**

### 2.2 BN Vital — percentile method (no fixed triad) · confidence HIGH
Esperado = **simple mean** of the series; Pesimista = **P25**; Optimista = **P75**, over the fund's **nominal annual** return series (ROP since ago-2003, market valuation). Inflación colones = BCCR meta 3±1%; dólares = trading-partner historical inflation. Aporte ROP 4,25%; ages 65/57. Density treated implicitly in the salary estimate.

### 2.3 Vida Plena / OPC-CCSS — percentile method, no numbers published · confidence HIGH
Scenarios by percentiles of SUPEN-published historical returns; "rendimiento nominal mensual"; age 65; Retiro Programado allows a ≤20% reserve for beneficiaries. No numeric triad published.

### 2.4 BCR Pensiones, BAC Pensiones — GAP
No public projection-methodology PDF located. Method/triad = OPEN (§7).

### 2.5 Realized ROP returns (context / sanity anchor — NOT a projection assumption)
Nominal annualized at **31-dic-2025** (3a / 5a / 10a), from Popular's official comparativo with SUPEN data:
`BAC 12,48/8,46/8,22 · BCR 9,51/7,74/8,08 · BN-Vital 11,01/8,30/8,53 · CCSS-OPC 9,25/7,27/8,13 · Popular 11,68/8,77/8,90 · Vida Plena 9,52/8,15/8,51 · Régimen(sistema) 11,11/8,41/8,56`. Comisión sobre saldo 0,35% all OPC.
- **The pesimista band is real territory:** Q1-2025 saw BN Vital fall 10,53% → 6,62% (aranceles + DeepSeek shock); Popular ~8,01% annual to abril-2025. Never omit the pesimista band in narration.

---

## 3. Macro (BCCR) + SUPEN projection mandate

### 3.1 BCCR inflation target · confidence HIGH
**3,0% ± 1 pp (range 2%–4%)** for 2026 (Comunicado CP-BCCR-003-2026, 30-ene-2026).
- ⚠️ **Under review in 2026** (possible re-formulation of the scheme; no alternative number yet). **2025 closed at −1,2% (deflation)**; models expect re-entry into the band ~**Q2-2027**. The "3%" discount is an *assumption*, not recent reality — version it by year.

### 3.2 SUPEN mandate (current_law) · confidence HIGH
- **SP-A-243-2021 (18-may-2021):** statements for affiliates with **≥5 years** permanence must carry the projected pension, in **3 scenarios** (pesimista/esperado/optimista), modality **Retiro Programado at present value** (`saldo ÷ VANU`, by sex).
- **SP-A-141-2010:** desacumulación modalities.
- **Mandatory disclaimer (verbatim, identical across Popular & OPC-CCSS → it is normed text):**
  > *"Las proyecciones tienen, exclusivamente, fines informativos o ilustrativos, sin que, de ninguna forma, constituya la promesa o compromiso, a cargo de la operadora, de pagar el monto estimado de pensión resultante."*

  **This is your D6/hope-anchor guardrail, stated by a regulated entity — reproduce it in B6 narration.**

---

## 4. IVM reform 2026 — PROPOSAL, NOT LAW (encode as a labeled alternative only)

- Presented in CCSS Board **sesión ordinaria 9603 (4-may-2026)** as **"20 insumos preliminares"**; the Board only ordered a **"Mesa Técnica Nacional"** to study them — **no reglamento adopted.**
- Proposes: replacement rate **40%–43%** (vs current 43%–52,5%); **360 cuotas** (vs 300, i.e. 30 vs 25 yrs); a **5% health contribution** from IVM pensioners; 3-pillar integration (IVM 40-43% + ROP 15-20% → ~60% standard).
- Estimated implementation (if approved): **H1-2028**. Analysis open. **NOT law.**
- **Encoding:** an optional `ivm_propuesta_2026` constant set labeled *"propuesta en discusión, no vigente"* → enables an honest "así se vería tu pensión si la reforma pasa" feature without presenting it as reality.

---

## 5. Fondos generacionales — APPROVED, in force **1-ene-2029**, not before

- Acuerdo Conassif **1838-2023 (6-dic-2023)**, approved firm; entry-into-force repeatedly postponed, **fixed to 1-ene-2029** (confirmed firm 27-ene-2026).
- 4 funds by birth year: **A (≤1969 + pensioners) · B (1970-79) · C (1980-89) · D (≥1990)**; mobility one level every 5 years (except A); life-cycle profile (younger = more equities, higher expected return + variance; older = fixed income / preservation).
- Per-cohort investment limits + return/variance curves **not yet published** (the studies that caused the postponement).
- **Encoding:** v1 does **not** need it (not law until 2029). Reserve a `cohort` axis in the constants schema; do **not** wire per-cohort curves yet.

---

## 6. Encoding recommendation for `app/domain/retirement/`

Mirror `app/domain/payroll/rates.py`: year-keyed dicts, `source` + `effective_date` on every set, **unconfigured year → raise** (never a silent stale year). Two engines, two natures:

```python
# IVM = deterministic LAW (formula). ROP = operator METHODOLOGY (versioned per OPC × year).

IVM_VIGENTE_2024 = {                       # status: current_law
    "cuantia_basica_brackets": [           # (upper_multiple_exclusive, rate)
        (2, 0.525), (3, 0.510), (4, 0.494), (5, 0.478),
        (6, 0.462), (8, 0.446), (None, 0.430),
    ],
    "cuantia_adicional_pct_per_month_over_300": 0.000833,  # +1%/yr over 25yr  (threshold 300: CONFIRM)
    "postergacion_pct_per_month": 0.001333,               # on the SPR (art. 23), NOT the pension
    "postergacion_cap_pct_of_spr": 1.25,                  # CONFIRM (125% vs "doble")
    "cuotas_full": 300, "edad": 65,
    "min_pension_crc": 162_295,           # feb-2026
    "max_pension_ordinaria_crc": 1_666_062,  # CONFIRM 2026 value
    "salmin_ocupacion_no_calificada_crc": None,  # 2026 divisor — OPEN (§7)
    "source": "Reglamento IVM arts. 23-29 (reforma 9229, vigente 11-ene-2024)",
}
IVM_PROPUESTA_2026 = {                      # status: proposal — NOT in force; feature-flag only
    "cuantia_basica_range": (0.40, 0.43), "cuotas_full": 360,
    "note": "Sesión JD 9603 (4-may-2026); Mesa Técnica; est. H1-2028. NO vigente.",
}

ROP_POPULAR_2026 = {                       # status: operator_methodology — re-verify each January
    "scenarios_nominal_crc": {"pesimista": 0.0406, "esperado": 0.0882, "optimista": 0.1322},
    "inflacion": 0.03, "comision_saldo_anual": 0.0035, "edad": 65,
    "aporte_rop": 0.0425,                  # 1% worker + 3.25% patrono (Ley 7983)
    "density": "percentiles P35(H)/P30(M) pes · P45 opt",  # NOT fixed 91.67/70.85/50
    "source": "Popular Pensiones — Manual de Proyecciones ENERO 2026",
}
COHORT_AXIS = None                         # reserved for fondos generacionales (1-ene-2029)
```

**Competitive edge (keep):** the OPCs assume a *generic* contribution density because they can't see the affiliate's life. Ledger **can** compute the user's *real observed density* from their ingresos/planilla and feed it as a deterministic metrics-layer signal, using the OPC percentile densities only as a pre-data fallback. A projection with real density beats the operator's own statement — and it's "the observed signal rules."

### 6.1 Narration guardrails (B6)
1. **Never a single retirement number — always the 3 bands** (SUPEN mandates it for regulated entities; a single confident figure violates D6/hope-anchor).
2. **Reproduce the disclaimer** (§3.2) — it's the regulator's own "estimation, not promise."
3. **Discount to present value** — subtract the inflation assumption explicitly; nominal → real, or the "esperado" over-sells.
4. **Narrate the constants version** ("con supuestos de enero 2026") so a stale projection never reads as current.
5. **Never drop the pesimista band** — 7,20%/6,62% is territory already visited in 2025.

---

## 7. OPEN — requires operator ⛳ sign-off against primary sources

1. Consolidated **arts. 23/24/25** (PGR SCIJ `nValor2=26485` or La Gaceta of the reform) to close: (a) cuantía-adicional threshold **240 vs 300**, (b) postergación cap **125% vs "doble"**, (c) the salary-count **transition** schedule.
2. **2026 salario mínimo de ocupación no calificada** (Decreto MTSS) — the divisor for the §1.2 table.
3. **Pensión máxima IVM 2026** exact vs SUPEN (₡1.666.062 base ~2025 → +~1,63%).
4. **BMC-IVM 2026** (₡324.590 vs ₡311.990) vs Decreto 45303-MTSS / La Gaceta 229.
5. **ROP triads of the other OPCs** (BN Vital recomputed, Vida Plena, BCR, BAC) if you want multi-operator; else Popular as the reference.
6. The ⛳ gate: sign the encoded numbers against the **primary Reglamento IVM** + the chosen OPC's methodology manual before B6 is user-visible.

---

## 8. Sources (primary first)

- Reglamento del Seguro de IVM (arts. 5, 23, 24, 25, 28, 29) — PGR SCIJ / CCSS; pre-reform copy: CEPAL/OIG `costa_rica_-_reglamento_sivm.pdf`.
- CICR — *Las últimas reformas al Régimen de IVM (dic-2021)* `cicr.com/.../Reforma_IVM_dic_2021.pdf`.
- Popular Pensiones — **Manual de Metodología de Proyecciones Enero 2026** `bancopopular.fi.cr/.../2026/01/Manual-de-Metologia-de-Proyecciones-Enero-2026.pdf` (+ 2024/04 & 2025/01 versions for the trail); **Comparativo de Rendimientos Diciembre 2025**.
- BN Vital — *Proyección de la pensión de los afiliados* `bnvital.com/hubfs/ProyeccionPensionBNVital.pdf`.
- OPC-CCSS / Vida Plena — *Metodología para el cálculo de la proyección de pensión complementaria* `opcccss.fi.cr/.../2024/07/...pdf`.
- SUPEN — Acuerdo SP-A-243-2021, SP-A-141-2010, Glosario de Estado de Cuenta, Montos de Pensión IVM.
- BCCR — Informe de Política Monetaria enero 2026 (CP-BCCR-003-2026).
- CCSS — noticia cotización IVM 2026; oficio GP-0607-2026 (pensión mínima).
- Reform proposal: CRHoy / Monumental / La Nación / Infobae (sesión 9603, 4-may-2026).
- Fondos generacionales: Conassif 1838-2023; El Financiero / CRHoy / OPC-CCSS / Popular FAQ (postponement to 2029, cohort structure).
- Contribution rates: BDO / Deloitte / El Observador (11,66% ene-2026).
