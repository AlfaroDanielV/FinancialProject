import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { displayMoney } from "../../lib/money";
import {
  CategoryBreakdownItem,
  DailyCashFlowPoint,
} from "../../schemas/dashboard";

interface DashboardChartsProps {
  daily: DailyCashFlowPoint[];
  categories: CategoryBreakdownItem[];
  currency: string;
  showDaily?: boolean;
  showCategories?: boolean;
}

function shortDay(value: string) {
  return value.slice(8, 10);
}

function shortCategory(value: string) {
  const normalized = value.replaceAll("_", " ");
  return normalized.length > 13 ? `${normalized.slice(0, 12)}…` : normalized;
}

export default function DashboardCharts({
  daily,
  categories,
  currency,
  showDaily = true,
  showCategories = true,
}: DashboardChartsProps) {
  const categoryData = categories.map((item) => ({
    category: shortCategory(item.category),
    income: item.income_total,
    expense: item.expense_total,
  }));

  return (
    <>
      {showDaily && <div className="h-32 w-full">
        {daily.length === 0 ? (
          <div className="flex h-full items-center justify-center rounded-md border border-dashed border-slate-300 text-sm text-slate-500">
            Sin movimientos este mes
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={daily} margin={{ top: 8, right: 4, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id="netFlow" x1="0" x2="0" y1="0" y2="1">
                  <stop offset="0%" stopColor="#0e7c4a" stopOpacity={0.28} />
                  <stop offset="100%" stopColor="#0e7c4a" stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <XAxis
                dataKey="date"
                tickFormatter={shortDay}
                tickLine={false}
                axisLine={false}
                minTickGap={12}
                tick={{ fill: "#64748b", fontSize: 11 }}
              />
              <YAxis hide domain={["auto", "auto"]} />
              <Tooltip
                formatter={(value) => displayMoney(Number(value), currency)}
                labelFormatter={(label) => `Día ${shortDay(String(label))}`}
                contentStyle={{
                  borderRadius: 8,
                  borderColor: "#cbd5e1",
                  fontSize: 12,
                }}
              />
              <Area
                type="monotone"
                dataKey="net_flow"
                stroke="#0e7c4a"
                strokeWidth={2}
                fill="url(#netFlow)"
                dot={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>}

      {showCategories && <div className="h-64 w-full">
        {categoryData.length === 0 ? (
          <div className="flex h-full items-center justify-center rounded-md border border-dashed border-slate-300 text-sm text-slate-500">
            Sin categorías en este periodo
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <BarChart
              data={categoryData}
              layout="vertical"
              margin={{ top: 8, right: 8, left: 4, bottom: 8 }}
              barGap={4}
            >
              <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#e2e8f0" />
              <XAxis
                type="number"
                tickLine={false}
                axisLine={false}
                tick={{ fill: "#64748b", fontSize: 11 }}
                tickFormatter={(value) => {
                  if (Number(value) >= 1000000) return `${Math.round(Number(value) / 1000000)}M`;
                  if (Number(value) >= 1000) return `${Math.round(Number(value) / 1000)}k`;
                  return String(value);
                }}
              />
              <YAxis
                dataKey="category"
                type="category"
                width={92}
                tickLine={false}
                axisLine={false}
                tick={{ fill: "#334155", fontSize: 12 }}
              />
              <Tooltip
                formatter={(value) => displayMoney(Number(value), currency)}
                contentStyle={{
                  borderRadius: 8,
                  borderColor: "#cbd5e1",
                  fontSize: 12,
                }}
              />
              <Bar dataKey="income" name="Ingresos" fill="#0e7c4a" radius={[0, 4, 4, 0]} />
              <Bar dataKey="expense" name="Gastos" fill="#b91c1c" radius={[0, 4, 4, 0]} />
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>}
    </>
  );
}
