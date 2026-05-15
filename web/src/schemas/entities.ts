import { z } from "zod";

import { moneyToApiString } from "../lib/money";

function parsePercentInput(value: string | undefined) {
  if (!value) return null;
  const cleaned = value.trim().replace("%", "").replace(",", ".");
  if (!cleaned) return null;
  const parsed = Number(cleaned);
  return Number.isFinite(parsed) ? parsed / 100 : null;
}

function parseWholeNumberInput(value: string | undefined) {
  if (!value) return null;
  const parsed = Number(value.trim());
  return Number.isInteger(parsed) ? parsed : null;
}

export const CurrencySchema = z.enum(["CRC", "USD"]);
export type Currency = z.infer<typeof CurrencySchema>;

export const AccountTypeSchema = z.enum([
  "checking",
  "savings",
  "credit",
  "investment",
]);
export type AccountType = z.infer<typeof AccountTypeSchema>;

export const Account = z.object({
  id: z.string().uuid(),
  user_id: z.string().uuid(),
  name: z.string(),
  account_type: AccountTypeSchema,
  currency: CurrencySchema,
  initial_balance: z.string(),
  is_active: z.boolean(),
  archived: z.boolean().optional().default(false),
  created_at: z.string(),
  current_balance: z.string().nullable().optional(),
  month_start_balance: z.string().nullable().optional(),
});
export type Account = z.infer<typeof Account>;

export const AccountList = z.array(Account);

export const Transaction = z.object({
  id: z.string().uuid(),
  user_id: z.string().uuid(),
  account_id: z.string().uuid().nullable(),
  transfer_id: z.string().uuid().nullable().optional(),
  category_id: z.string().uuid().nullable().optional(),
  amount: z.number(),
  currency: z.string(),
  merchant: z.string().nullable(),
  description: z.string().nullable(),
  category: z.string().nullable(),
  subcategory: z.string().nullable(),
  transaction_date: z.string(),
  source: z.string(),
  parse_status: z.string(),
  status: z.string(),
  is_duplicate: z.boolean(),
  archived: z.boolean().optional().default(false),
  created_at: z.string(),
});
export type Transaction = z.infer<typeof Transaction>;

export const TransactionList = z.object({
  total: z.number().int(),
  items: z.array(Transaction),
  next_cursor: z.string().nullable().optional(),
});
export type TransactionList = z.infer<typeof TransactionList>;

export const UserCategoryItem = z.object({
  id: z.string().uuid(),
  name: z.string(),
  kind: z.string(),
  color: z.string(),
  icon: z.string().nullable().optional(),
  is_default: z.boolean(),
  archived: z.boolean(),
});
export type UserCategoryItem = z.infer<typeof UserCategoryItem>;

export const UserCategoryList = z.array(UserCategoryItem);

export const GoalDetail = z.object({
  id: z.string().uuid(),
  user_id: z.string().uuid(),
  name: z.string(),
  target_amount: z.string(),
  target_currency: CurrencySchema,
  current_amount: z.string(),
  target_date: z.string().nullable(),
  deadline: z.string().nullable().optional(),
  monthly_contribution: z.string().nullable(),
  priority: z.number().int(),
  status: z.string(),
  linked_account_id: z.string().uuid().nullable(),
  created_at: z.string(),
});
export type GoalDetail = z.infer<typeof GoalDetail>;

export const GoalDetailList = z.array(GoalDetail);

export const GoalContribution = z.object({
  id: z.string().uuid(),
  goal_id: z.string().uuid(),
  transaction_id: z.string().uuid().nullable(),
  amount: z.string(),
  occurred_at: z.string(),
  created_at: z.string(),
});
export type GoalContribution = z.infer<typeof GoalContribution>;

export const GoalContributionList = z.array(GoalContribution);

export const GoalForecast = z.object({
  goal_id: z.string().uuid(),
  target_amount: z.string(),
  current_amount: z.string(),
  remaining: z.string(),
  avg_monthly_contribution: z.string(),
  months_to_target: z.number().int().nullable(),
  projected_completion_date: z.string().nullable(),
  has_enough_data: z.boolean(),
  lookback_months: z.number().int(),
});
export type GoalForecast = z.infer<typeof GoalForecast>;

export const AccountForm = z
  .object({
    name: z.string().trim().min(2, "Poné al menos 2 caracteres."),
    account_type: AccountTypeSchema,
    currency: CurrencySchema,
    initial_balance: z.string().trim().min(1, "Poné el saldo inicial."),
  })
  .superRefine((data, ctx) => {
    const parsed = moneyToApiString(data.initial_balance, data.currency);
    if (parsed === null) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["initial_balance"],
        message: "Poné un monto válido.",
      });
      return;
    }
    if (data.account_type !== "credit" && Number(parsed) < 0) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["initial_balance"],
        message: "Solo una cuenta de crédito puede arrancar en negativo.",
      });
    }
  });
export type AccountForm = z.infer<typeof AccountForm>;

export const IncomeTypeSchema = z.enum([
  "salary",
  "aguinaldo",
  "salario_escolar",
  "freelance",
  "other",
]);
export type IncomeType = z.infer<typeof IncomeTypeSchema>;

export const IncomeFrequencySchema = z.enum([
  "weekly",
  "biweekly",
  "monthly",
  "annual",
]);
export type IncomeFrequency = z.infer<typeof IncomeFrequencySchema>;

export const RecurringIncome = z.object({
  id: z.string().uuid(),
  user_id: z.string().uuid(),
  name: z.string(),
  income_type: IncomeTypeSchema,
  amount: z.string().nullable(),
  currency: CurrencySchema,
  frequency: IncomeFrequencySchema,
  next_payment_date: z.string(),
  base_salary_link_id: z.string().uuid().nullable(),
  is_active: z.boolean(),
  archived: z.boolean().optional().default(false),
  notes: z.string().nullable(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type RecurringIncome = z.infer<typeof RecurringIncome>;

export const RecurringIncomeList = z.array(RecurringIncome);

export const DerivedIncomeTypes = new Set<IncomeType>([
  "aguinaldo",
  "salario_escolar",
]);

export const IncomeForm = z
  .object({
    name: z.string().trim().min(2, "Poné al menos 2 caracteres."),
    income_type: IncomeTypeSchema,
    currency: CurrencySchema,
    amount: z.string().trim().optional(),
    frequency: IncomeFrequencySchema,
    next_payment_date: z.string().min(1, "Elegí una fecha."),
    base_salary_link_id: z.string().optional(),
    notes: z.string().trim().optional(),
  })
  .superRefine((data, ctx) => {
    if (DerivedIncomeTypes.has(data.income_type)) {
      if (!data.base_salary_link_id) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["base_salary_link_id"],
          message: "Escogé el salario base.",
        });
      }
      return;
    }

    if (!data.amount) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["amount"],
        message: "Poné el monto.",
      });
      return;
    }
    const parsed = moneyToApiString(data.amount, data.currency);
    if (parsed === null || Number(parsed) <= 0) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["amount"],
        message: "Poné un monto mayor a cero.",
      });
    }
  });
export type IncomeForm = z.infer<typeof IncomeForm>;

export const CategoriesResponse = z.object({
  categories: z.array(z.string().min(1)),
});
export type CategoriesResponse = z.infer<typeof CategoriesResponse>;

export const BillFrequencySchema = z.enum([
  "weekly",
  "biweekly",
  "monthly",
  "bimonthly",
  "quarterly",
  "semiannual",
  "annual",
  "custom",
]);
export type BillFrequency = z.infer<typeof BillFrequencySchema>;

export const BillFormFrequencySchema = z.enum([
  "monthly",
  "biweekly",
  "annual",
  "custom",
]);
export type BillFormFrequency = z.infer<typeof BillFormFrequencySchema>;

export const RecurringBill = z.object({
  id: z.string().uuid(),
  name: z.string(),
  provider: z.string().nullable(),
  category: z.string(),
  amount_expected: z.number().nullable(),
  currency: CurrencySchema,
  is_variable_amount: z.boolean(),
  account_id: z.string().uuid().nullable(),
  frequency: BillFrequencySchema,
  day_of_month: z.number().int().nullable(),
  recurrence_rule: z.string().nullable(),
  start_date: z.string(),
  end_date: z.string().nullable(),
  lead_time_days: z.number().int(),
  is_active: z.boolean(),
  notes: z.string().nullable(),
  linked_loan_id: z.string().uuid().nullable(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type RecurringBill = z.infer<typeof RecurringBill>;

export const RecurringBillList = z.array(RecurringBill);

export const BillForm = z
  .object({
    name: z.string().trim().min(2, "Poné al menos 2 caracteres."),
    provider: z.string().trim().optional(),
    category: z.string().trim().min(1, "Escogé una categoría."),
    currency: CurrencySchema,
    amount_expected: z.string().trim().optional(),
    is_variable_amount: z.boolean(),
    frequency: BillFormFrequencySchema,
    day_of_month: z.string().trim().optional(),
    recurrence_rule: z.string().trim().optional(),
    start_date: z.string().min(1, "Elegí la próxima fecha."),
    lead_time_days: z.string().trim().optional(),
    notes: z.string().trim().optional(),
  })
  .superRefine((data, ctx) => {
    const amount = moneyToApiString(data.amount_expected ?? "", data.currency);
    const day = parseWholeNumberInput(data.day_of_month);
    const lead = parseWholeNumberInput(data.lead_time_days || "0");

    if (!data.is_variable_amount && (amount === null || Number(amount) <= 0)) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["amount_expected"],
        message: "Poné un monto mayor a cero.",
      });
    }
    if (
      data.frequency !== "custom" &&
      data.frequency !== "biweekly" &&
      (day === null || day < 1 || day > 31)
    ) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["day_of_month"],
        message: "El día va de 1 a 31.",
      });
    }
    if (data.frequency === "custom" && !data.recurrence_rule) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["recurrence_rule"],
        message: "Poné la regla RRULE para esta frecuencia.",
      });
    }
    if (lead === null || lead < 0 || lead > 90) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["lead_time_days"],
        message: "El aviso va de 0 a 90 días.",
      });
    }
  });
export type BillForm = z.infer<typeof BillForm>;

export const DebtTypeSchema = z.enum([
  "mortgage",
  "credit_card_balance",
  "credit_card",
  "auto_loan",
  "personal_loan",
  "student_loan",
  "other",
]);
export type DebtType = z.infer<typeof DebtTypeSchema>;

export const RateTypeSchema = z.enum(["fixed", "variable"]);
export type RateType = z.infer<typeof RateTypeSchema>;

export const Debt = z.object({
  id: z.string().uuid(),
  user_id: z.string().uuid(),
  account_id: z.string().uuid().nullable(),
  name: z.string(),
  debt_type: DebtTypeSchema,
  lender: z.string().nullable(),
  original_amount: z.number(),
  current_balance: z.number(),
  interest_rate: z.number(),
  minimum_payment: z.number(),
  payment_due_day: z.number().int(),
  term_months: z.number().int().nullable(),
  start_date: z.string().nullable(),
  maturity_date: z.string().nullable(),
  currency: CurrencySchema,
  notes: z.string().nullable(),
  rate_type: RateTypeSchema,
  rate_reference: z.string().nullable(),
  rate_spread: z.number().nullable(),
  prepayment_penalty_pct: z.number(),
  payments_made: z.number().int(),
  includes_insurance: z.boolean(),
  insurance_monthly: z.number().nullable(),
  is_active: z.boolean(),
  archived: z.boolean().optional().default(false),
  created_at: z.string(),
  updated_at: z.string(),
});
export type Debt = z.infer<typeof Debt>;

export const DebtSummary = z.object({
  id: z.string().uuid(),
  name: z.string(),
  debt_type: DebtTypeSchema,
  current_balance: z.number(),
  interest_rate: z.number(),
  minimum_payment: z.number(),
  payment_due_day: z.number().int(),
  is_active: z.boolean(),
  archived: z.boolean().optional().default(false),
});
export type DebtSummary = z.infer<typeof DebtSummary>;

export const DebtSummaryList = z.array(DebtSummary);

export const DebtOverviewSchema = z.object({
  total_debt: z.number(),
  total_minimum_monthly: z.number(),
  debts_by_type: z.record(z.string(), z.number()),
  upcoming_payments: z.array(
    z.object({
      debt_id: z.string().uuid(),
      name: z.string(),
      debt_type: z.string(),
      minimum_payment: z.number(),
      payment_due_day: z.number().int(),
      current_balance: z.number(),
    }),
  ),
});
export type DebtOverviewSchema = z.infer<typeof DebtOverviewSchema>;

export const ScheduleSummary = z.object({
  monthly_payment: z.number(),
  total_months: z.number().int(),
  total_interest: z.number(),
  total_paid: z.number(),
  payoff_date: z.string().nullable(),
});
export type ScheduleSummary = z.infer<typeof ScheduleSummary>;

export const PayoffScenarioResult = z.object({
  months_saved: z.number().int(),
  interest_saved: z.number(),
  total_saved: z.number(),
  new_payoff_date: z.string().nullable(),
  new_total_months: z.number().int(),
  new_total_interest: z.number(),
  prepayment_penalty_applies: z.boolean(),
  prepayment_penalty_amount: z.number(),
});
export type PayoffScenarioResult = z.infer<typeof PayoffScenarioResult>;

export const PayoffScenariosResponse = z.object({
  debt_id: z.string().uuid(),
  currency: CurrencySchema,
  payments_made: z.number().int(),
  prepayment_penalty_pct: z.number(),
  original: ScheduleSummary,
  lump_sum: PayoffScenarioResult.nullable(),
  extra_monthly: PayoffScenarioResult.nullable(),
  variable_rate_notice: z.string().nullable().optional(),
});
export type PayoffScenariosResponse = z.infer<typeof PayoffScenariosResponse>;

export const AmortizationRow = z.object({
  month_number: z.number().int(),
  payment_date: z.string(),
  payment_amount: z.number(),
  principal_portion: z.number(),
  interest_portion: z.number(),
  insurance_portion: z.number(),
  extra_payment: z.number(),
  remaining_balance: z.number(),
  cumulative_interest: z.number(),
  cumulative_principal: z.number(),
});
export type AmortizationRow = z.infer<typeof AmortizationRow>;

export const AmortizationSchedule = z.object({
  debt_id: z.string().uuid(),
  debt_name: z.string(),
  current_balance: z.number(),
  interest_rate: z.number(),
  monthly_payment: z.number(),
  total_months: z.number().int(),
  total_interest: z.number(),
  total_principal: z.number(),
  total_payments: z.number(),
  payoff_date: z.string().nullable(),
  variable_rate_notice: z.string().nullable().optional(),
  schedule: z.array(AmortizationRow),
});
export type AmortizationSchedule = z.infer<typeof AmortizationSchedule>;

export const DebtForm = z
  .object({
    name: z.string().trim().min(2, "Poné al menos 2 caracteres."),
    debt_type: DebtTypeSchema,
    lender: z.string().trim().optional(),
    currency: CurrencySchema,
    original_amount: z.string().trim().min(1, "Poné el monto original."),
    current_balance: z.string().trim().min(1, "Poné el saldo actual."),
    interest_rate: z.string().trim().min(1, "Poné la tasa anual."),
    minimum_payment: z.string().trim().min(1, "Poné la cuota mensual."),
    payment_due_day: z.string().trim().min(1, "Poné el día de pago."),
    term_months: z.string().trim().min(1, "Poné el plazo en meses."),
    start_date: z.string().optional(),
    rate_type: RateTypeSchema,
    rate_reference: z.string().trim().optional(),
    rate_spread: z.string().trim().optional(),
    prepayment_penalty_pct: z.string().trim().optional(),
    payments_made: z.string().trim().optional(),
    includes_insurance: z.boolean(),
    insurance_monthly: z.string().trim().optional(),
    notes: z.string().trim().optional(),
  })
  .superRefine((data, ctx) => {
    const original = moneyToApiString(data.original_amount, data.currency);
    const current = moneyToApiString(data.current_balance, data.currency);
    const minimum = moneyToApiString(data.minimum_payment, data.currency);
    const insurance = data.includes_insurance
      ? moneyToApiString(data.insurance_monthly ?? "", data.currency)
      : "0.00";
    const annualRate = parsePercentInput(data.interest_rate);
    const rateSpread = parsePercentInput(data.rate_spread);
    const prepaymentPenalty = parsePercentInput(data.prepayment_penalty_pct || "0");
    const paymentDay = parseWholeNumberInput(data.payment_due_day);
    const term = parseWholeNumberInput(data.term_months);
    const paymentsMade = parseWholeNumberInput(data.payments_made || "0");

    if (original === null || Number(original) <= 0) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["original_amount"],
        message: "Poné un monto original mayor a cero.",
      });
    }
    if (current === null || Number(current) <= 0) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["current_balance"],
        message: "Poné un saldo actual mayor a cero.",
      });
    }
    if (annualRate === null || annualRate < 0 || annualRate > 1) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["interest_rate"],
        message: "Poné una tasa anual entre 0% y 100%.",
      });
    }
    if (minimum === null || Number(minimum) <= 0) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["minimum_payment"],
        message: "Poné una cuota mayor a cero.",
      });
    }
    if (paymentDay === null || paymentDay < 1 || paymentDay > 31) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["payment_due_day"],
        message: "El día de pago va de 1 a 31.",
      });
    }
    if (term === null || term <= 0) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["term_months"],
        message: "Poné un plazo mayor a cero.",
      });
    }
    if (data.rate_type === "variable" && !data.rate_reference) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["rate_reference"],
        message: "Poné la referencia de la tasa variable.",
      });
    }
    if (data.rate_type === "variable" && rateSpread !== null && rateSpread > 1) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["rate_spread"],
        message: "El spread no puede ser mayor a 100%.",
      });
    }
    if (prepaymentPenalty === null || prepaymentPenalty < 0 || prepaymentPenalty > 0.05) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["prepayment_penalty_pct"],
        message: "La comisión no puede superar 5%.",
      });
    }
    if (paymentsMade === null || paymentsMade < 0) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["payments_made"],
        message: "Poné un número de cuotas pagadas válido.",
      });
    }
    if (data.includes_insurance && (insurance === null || Number(insurance) < 0)) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["insurance_monthly"],
        message: "Poné un monto de seguro válido.",
      });
    }

    if (current !== null && minimum !== null && annualRate !== null) {
      const insuranceAmount = insurance === null ? 0 : Number(insurance);
      const effectivePayment = Number(minimum) - insuranceAmount;
      const monthlyInterest = Number(current) * (annualRate / 12);
      if (annualRate > 0 && effectivePayment <= monthlyInterest) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["minimum_payment"],
          message: "La cuota no cubre los intereses mensuales.",
        });
      }
    }
  });
export type DebtForm = z.infer<typeof DebtForm>;

export function percentToApiDecimal(value: string | undefined) {
  const parsed = parsePercentInput(value || "0");
  return parsed === null ? null : Number(parsed.toFixed(4));
}

export function wholeNumberToApi(value: string | undefined) {
  return parseWholeNumberInput(value);
}
