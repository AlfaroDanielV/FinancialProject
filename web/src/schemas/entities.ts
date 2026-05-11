import { z } from "zod";

import { moneyToApiString } from "../lib/money";

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
  created_at: z.string(),
});
export type Account = z.infer<typeof Account>;

export const AccountList = z.array(Account);

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
