import { api } from "./client";

/**
 * CR net-salary calculator — thin client over `POST /payroll/net-salary`.
 *
 * The tax math (CCSS + ISR brackets + créditos) lives ONLY in the backend
 * (`app/domain/payroll`). The mobile app never reimplements it: the brackets
 * change annually and a TS copy would silently drift. One deep endpoint, one
 * source of truth.
 */

export interface NetSalaryRequest {
  gross_monthly: number;
  year?: number;
  hijos?: number;
  conyuge?: boolean;
  isr_base_mode?: "gross" | "gross_minus_ccss";
  solidarista_pct?: number;
}

export interface IsrTramoDetail {
  tramo: number;
  rate: number;
  taxable_in_tramo: number;
  tax_in_tramo: number;
}

export interface NetSalaryBreakdown {
  gross_monthly: number;
  ccss: { sem: number; ivm: number; banco_popular: number; total: number };
  isr: {
    base: number;
    tax_before_credits: number;
    creditos_aplicados: number;
    tax: number;
    tramos: IsrTramoDetail[];
  };
  solidarista: number;
  net_monthly: number;
  effective_rate: number;
  year: number;
  isr_base_mode: string;
}

export async function computeNetSalary(
  req: NetSalaryRequest,
): Promise<NetSalaryBreakdown> {
  const { data } = await api.post<NetSalaryBreakdown>(
    "/payroll/net-salary",
    req,
  );
  return data;
}
