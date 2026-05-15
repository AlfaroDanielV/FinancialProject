import { AxiosError } from "axios";
import { useState } from "react";
import { Link } from "react-router-dom";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import {
  deleteAllInsights,
  deleteInsight,
  deleteInsightsByGroup,
  downloadInsightsExport,
  fetchAllInsights,
  InsightGroupKey,
  InsightItem,
  updateInsightContent,
} from "../api/memory";

const LOW_CONFIDENCE_THRESHOLD = 0.5;

function apiErrorMessage(error: unknown, fallback: string) {
  if (error instanceof AxiosError) {
    const detail = error.response?.data?.detail;
    if (typeof detail === "string") return detail;
  }
  return fallback;
}

function formatLongDate(iso: string) {
  const [year, month, day] = iso.slice(0, 10).split("-").map(Number);
  return new Intl.DateTimeFormat("es-CR", {
    day: "numeric",
    month: "long",
    year: "numeric",
  }).format(new Date(year, month - 1, day));
}

function sourceLabel(source: string) {
  if (source === "computed") return "Yo lo calculo";
  if (source === "llm_extracted") return "Inferido";
  if (source === "user_override") return "Tu definición";
  return source;
}

export default function MemoryIndex() {
  const queryClient = useQueryClient();
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [editingInsight, setEditingInsight] = useState<InsightItem | null>(null);
  const [serverError, setServerError] = useState<string | null>(null);
  const [deleteAllStep, setDeleteAllStep] = useState<0 | 1 | 2>(0);

  const memoryQuery = useQuery({
    queryKey: ["memory", "list"],
    queryFn: () => fetchAllInsights(false),
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["memory"] });

  const deleteSingleMutation = useMutation({
    mutationFn: (id: string) => deleteInsight(id),
    onSuccess: invalidate,
    onError: (err) =>
      setServerError(apiErrorMessage(err, "No pudimos eliminar.")),
  });

  const deleteGroupMutation = useMutation({
    mutationFn: (group: InsightGroupKey) => deleteInsightsByGroup(group),
    onSuccess: invalidate,
    onError: (err) =>
      setServerError(apiErrorMessage(err, "No pudimos eliminar el grupo.")),
  });

  const deleteAllMutation = useMutation({
    mutationFn: () => deleteAllInsights(),
    onSuccess: () => {
      setDeleteAllStep(0);
      invalidate();
    },
    onError: (err) => {
      setDeleteAllStep(0);
      setServerError(apiErrorMessage(err, "No pudimos borrar la memoria."));
    },
  });

  const exportMutation = useMutation({
    mutationFn: () => downloadInsightsExport(),
    onError: (err) =>
      setServerError(apiErrorMessage(err, "No pudimos descargar el export.")),
  });

  const data = memoryQuery.data;

  return (
    <div className="space-y-5">
      <header className="flex flex-col gap-2">
        <Link to="/" className="text-sm font-medium text-accent hover:text-accent-dark">
          Volver al inicio
        </Link>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-slate-900">Memoria</h1>
            <p className="mt-1 text-slate-600">
              Lo que el agente sabe sobre vos. Mismo contenido que ves con{" "}
              <code>/memoria</code> en el bot, editable acá.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => exportMutation.mutate()}
              disabled={exportMutation.isPending}
              className="inline-flex min-h-11 items-center justify-center rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-800 hover:bg-slate-50 disabled:opacity-60"
            >
              {exportMutation.isPending ? "Descargando..." : "Descargar mi memoria"}
            </button>
          </div>
        </div>
      </header>

      {serverError && (
        <p className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
          {serverError}
        </p>
      )}

      {memoryQuery.isLoading && <p className="text-slate-500">Cargando...</p>}
      {memoryQuery.isError && (
        <p className="text-red-700">No pudimos cargar tu memoria.</p>
      )}
      {data && data.total === 0 && (
        <div className="rounded-md border border-slate-200 bg-white p-6 text-center text-slate-600">
          <p>
            Todavía no tengo nada anotado sobre vos. Hablá con el bot un
            rato y se va llenando solo.
          </p>
        </div>
      )}

      {data &&
        data.groups.map((group) => (
          <section
            key={group.group}
            className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm"
          >
            <header className="flex items-center justify-between">
              <button
                type="button"
                onClick={() =>
                  setCollapsed((prev) => ({
                    ...prev,
                    [group.group]: !prev[group.group],
                  }))
                }
                className="text-base font-semibold text-slate-900"
              >
                {group.label}{" "}
                <span className="text-sm font-normal text-slate-500">
                  ({group.items.length})
                </span>
              </button>
              <button
                type="button"
                onClick={() => {
                  if (
                    window.confirm(
                      `Borrar todos los insights de "${group.label}"?`,
                    )
                  ) {
                    deleteGroupMutation.mutate(group.group as InsightGroupKey);
                  }
                }}
                className="min-h-11 rounded-md border border-red-200 bg-white px-3 py-1.5 text-xs font-semibold text-red-700 hover:bg-red-50"
              >
                Borrar grupo
              </button>
            </header>

            {!collapsed[group.group] && (
              <ul className="mt-3 space-y-2">
                {group.items.map((item) => (
                  <InsightRow
                    key={item.id}
                    item={item}
                    onEdit={() => {
                      setServerError(null);
                      setEditingInsight(item);
                    }}
                    onDelete={() => {
                      if (
                        window.confirm(
                          "Borrar este insight? Se recalculará si es computado.",
                        )
                      ) {
                        deleteSingleMutation.mutate(item.id);
                      }
                    }}
                  />
                ))}
              </ul>
            )}
          </section>
        ))}

      {data && data.total > 0 && (
        <section className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-900">
          <p className="font-semibold">Borrar toda mi memoria</p>
          <p className="mt-1">
            Esto elimina todos los insights de manera definitiva. Los
            computados se vuelven a calcular en el siguiente ciclo.
          </p>
          {deleteAllStep === 0 && (
            <button
              type="button"
              onClick={() => setDeleteAllStep(1)}
              className="mt-3 min-h-11 rounded-md border border-red-300 bg-white px-4 py-2 text-sm font-semibold text-red-800 hover:bg-red-100"
            >
              Empezar
            </button>
          )}
          {deleteAllStep === 1 && (
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => setDeleteAllStep(2)}
                className="min-h-11 rounded-md border border-red-300 bg-white px-4 py-2 text-sm font-semibold text-red-800 hover:bg-red-100"
              >
                Confirmar (1/2)
              </button>
              <button
                type="button"
                onClick={() => setDeleteAllStep(0)}
                className="min-h-11 rounded-md px-4 py-2 text-sm font-semibold text-red-700"
              >
                Cancelar
              </button>
            </div>
          )}
          {deleteAllStep === 2 && (
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => deleteAllMutation.mutate()}
                disabled={deleteAllMutation.isPending}
                className="min-h-11 rounded-md bg-red-700 px-4 py-2 text-sm font-semibold text-white hover:bg-red-800 disabled:opacity-60"
              >
                {deleteAllMutation.isPending
                  ? "Borrando..."
                  : "Borrar todo (2/2)"}
              </button>
              <button
                type="button"
                onClick={() => setDeleteAllStep(0)}
                className="min-h-11 rounded-md px-4 py-2 text-sm font-semibold text-red-700"
              >
                Cancelar
              </button>
            </div>
          )}
        </section>
      )}

      {editingInsight && (
        <EditInsightModal
          item={editingInsight}
          onClose={() => setEditingInsight(null)}
          onSaved={() => {
            invalidate();
            setEditingInsight(null);
          }}
          onError={(message) => setServerError(message)}
        />
      )}
    </div>
  );
}

function InsightRow({
  item,
  onEdit,
  onDelete,
}: {
  item: InsightItem;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const confidence = Number(item.confidence);
  const lowConfidence = confidence < LOW_CONFIDENCE_THRESHOLD;
  return (
    <li className="rounded-md border border-slate-100 bg-slate-50 p-3">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex-1 space-y-1">
          <p className="text-sm text-slate-900">{item.description}</p>
          <div className="flex flex-wrap gap-2 text-xs">
            <span className="rounded-full bg-white px-2 py-0.5 text-slate-600">
              Confianza {(confidence * 100).toFixed(0)}%
            </span>
            <span className="rounded-full bg-white px-2 py-0.5 text-slate-600">
              {sourceLabel(item.source)}
            </span>
            {item.user_locked && (
              <span className="rounded-full bg-amber-100 px-2 py-0.5 text-amber-800">
                📌 tu definición
              </span>
            )}
            {lowConfidence && (
              <span className="rounded-full bg-amber-100 px-2 py-0.5 text-amber-800">
                ⚠️ poca evidencia
              </span>
            )}
            <span className="rounded-full bg-white px-2 py-0.5 text-slate-500">
              Vence {formatLongDate(item.valid_until)}
            </span>
          </div>
        </div>
        <div className="flex flex-wrap justify-end gap-2">
          {item.editable ? (
            <button
              type="button"
              onClick={onEdit}
              className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50"
            >
              Editar
            </button>
          ) : (
            <span className="rounded-md bg-white px-3 py-1.5 text-xs text-slate-500">
              Esto lo calculo yo
            </span>
          )}
          <button
            type="button"
            onClick={onDelete}
            className="min-h-11 rounded-md border border-red-200 bg-white px-3 py-1.5 text-xs font-semibold text-red-700 hover:bg-red-50"
          >
            Borrar
          </button>
        </div>
      </div>
    </li>
  );
}

const RISK_POSTURES = ["conservative", "moderate", "aggressive"] as const;
const DECISION_STYLES = ["analytical", "intuitive", "avoidant"] as const;
const LITERACY_LEVELS = ["beginner", "intermediate", "advanced"] as const;
const STATED_GOAL_STATUSES = ["mentioned", "committed", "abandoned"] as const;
const STATED_PREFERENCE_TOPICS = [
  "debt_payoff",
  "savings",
  "risk_tolerance",
  "lifestyle",
] as const;

function EditInsightModal({
  item,
  onClose,
  onSaved,
  onError,
}: {
  item: InsightItem;
  onClose: () => void;
  onSaved: () => void;
  onError: (message: string) => void;
}) {
  const mutation = useMutation({
    mutationFn: (content: Record<string, unknown>) =>
      updateInsightContent(item.id, content),
    onSuccess: () => onSaved(),
    onError: (err) =>
      onError(apiErrorMessage(err, "No pudimos guardar el cambio.")),
  });

  return (
    <div className="fixed inset-0 z-30 flex items-end justify-center bg-slate-900/40 p-4 sm:items-center">
      <div className="w-full max-w-md space-y-3 rounded-lg bg-white p-5 shadow-lg">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-slate-900">
            Editar insight
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="min-h-11 rounded-md px-2 text-slate-600"
          >
            Cerrar
          </button>
        </div>

        <p className="text-sm text-slate-600">{item.description}</p>
        <p className="text-xs text-slate-500">
          Al guardar marcamos esto como tu definición (📌). El extractor
          ya no la sobrescribe.
        </p>

        {item.insight_type === "risk_posture" && (
          <RiskPostureForm item={item} mutation={mutation} />
        )}
        {item.insight_type === "decision_style" && (
          <DecisionStyleForm item={item} mutation={mutation} />
        )}
        {item.insight_type === "financial_literacy" && (
          <FinancialLiteracyForm item={item} mutation={mutation} />
        )}
        {item.insight_type === "archetype" && (
          <ArchetypeForm item={item} mutation={mutation} />
        )}
        {item.insight_type === "stated_preference" && (
          <StatedPreferenceForm item={item} mutation={mutation} />
        )}
        {item.insight_type === "stated_goal" && (
          <StatedGoalForm item={item} mutation={mutation} />
        )}
      </div>
    </div>
  );
}

type MutationHandle = {
  mutate: (content: Record<string, unknown>) => void;
  isPending: boolean;
};

function SubmitRow({
  mutation,
  onClose,
}: {
  mutation: MutationHandle;
  onClose?: () => void;
}) {
  return (
    <div className="flex flex-wrap gap-2 pt-2">
      <button
        type="submit"
        disabled={mutation.isPending}
        className="min-h-11 rounded-md bg-accent px-4 py-2 text-sm font-semibold text-white hover:bg-accent-dark disabled:bg-slate-300"
      >
        {mutation.isPending ? "Guardando..." : "Guardar"}
      </button>
      {onClose && (
        <button
          type="button"
          onClick={onClose}
          className="min-h-11 rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50"
        >
          Cancelar
        </button>
      )}
    </div>
  );
}

function RiskPostureForm({
  item,
  mutation,
}: {
  item: InsightItem;
  mutation: MutationHandle;
}) {
  const [posture, setPosture] = useState(
    (item.content.posture as string) ?? "moderate",
  );
  return (
    <form
      className="space-y-3"
      onSubmit={(event) => {
        event.preventDefault();
        mutation.mutate({
          type: "risk_posture",
          posture,
          evidence_basis: "stated",
        });
      }}
    >
      <fieldset className="space-y-1 text-sm">
        <legend className="font-medium text-slate-700">Postura ante el riesgo</legend>
        {RISK_POSTURES.map((value) => (
          <label key={value} className="flex items-center gap-2">
            <input
              type="radio"
              name="posture"
              value={value}
              checked={posture === value}
              onChange={(event) => setPosture(event.target.value)}
            />
            <span>{value}</span>
          </label>
        ))}
      </fieldset>
      <SubmitRow mutation={mutation} />
    </form>
  );
}

function DecisionStyleForm({
  item,
  mutation,
}: {
  item: InsightItem;
  mutation: MutationHandle;
}) {
  const [style, setStyle] = useState(
    (item.content.style as string) ?? "analytical",
  );
  const existingEvidence = (item.content.evidence as string) ?? "tu propia definición";
  return (
    <form
      className="space-y-3"
      onSubmit={(event) => {
        event.preventDefault();
        mutation.mutate({
          type: "decision_style",
          style,
          evidence: existingEvidence,
        });
      }}
    >
      <fieldset className="space-y-1 text-sm">
        <legend className="font-medium text-slate-700">Estilo de decisión</legend>
        {DECISION_STYLES.map((value) => (
          <label key={value} className="flex items-center gap-2">
            <input
              type="radio"
              name="style"
              value={value}
              checked={style === value}
              onChange={(event) => setStyle(event.target.value)}
            />
            <span>{value}</span>
          </label>
        ))}
      </fieldset>
      <SubmitRow mutation={mutation} />
    </form>
  );
}

function FinancialLiteracyForm({
  item,
  mutation,
}: {
  item: InsightItem;
  mutation: MutationHandle;
}) {
  const [level, setLevel] = useState(
    (item.content.level as string) ?? "intermediate",
  );
  const existingEvidence = (item.content.evidence as string) ?? "tu propia definición";
  return (
    <form
      className="space-y-3"
      onSubmit={(event) => {
        event.preventDefault();
        mutation.mutate({
          type: "financial_literacy",
          level,
          evidence: existingEvidence,
        });
      }}
    >
      <fieldset className="space-y-1 text-sm">
        <legend className="font-medium text-slate-700">Nivel financiero</legend>
        {LITERACY_LEVELS.map((value) => (
          <label key={value} className="flex items-center gap-2">
            <input
              type="radio"
              name="level"
              value={value}
              checked={level === value}
              onChange={(event) => setLevel(event.target.value)}
            />
            <span>{value}</span>
          </label>
        ))}
      </fieldset>
      <SubmitRow mutation={mutation} />
    </form>
  );
}

function ArchetypeForm({
  item,
  mutation,
}: {
  item: InsightItem;
  mutation: MutationHandle;
}) {
  const [primary, setPrimary] = useState(
    (item.content.primary as string) ?? "ahorrador",
  );
  const [secondary, setSecondary] = useState(
    (item.content.secondary as string) ?? "",
  );
  const existingConfidence =
    (item.content.confidence as string | number) ?? "0.85";
  const existingEvidence =
    (item.content.evidence_summary as string) ?? "tu propia definición";
  return (
    <form
      className="space-y-3"
      onSubmit={(event) => {
        event.preventDefault();
        const payload: Record<string, unknown> = {
          type: "archetype",
          primary,
          confidence: String(existingConfidence),
          evidence_summary: existingEvidence,
        };
        if (secondary.trim()) payload.secondary = secondary.trim();
        mutation.mutate(payload);
      }}
    >
      <label className="block space-y-1 text-sm">
        <span className="font-medium text-slate-700">Arquetipo principal</span>
        <input
          className="w-full rounded-md border border-slate-300 px-3 py-2"
          value={primary}
          onChange={(event) => setPrimary(event.target.value)}
          required
          maxLength={64}
        />
      </label>
      <label className="block space-y-1 text-sm">
        <span className="font-medium text-slate-700">Secundario (opcional)</span>
        <input
          className="w-full rounded-md border border-slate-300 px-3 py-2"
          value={secondary}
          onChange={(event) => setSecondary(event.target.value)}
          maxLength={64}
        />
      </label>
      <SubmitRow mutation={mutation} />
    </form>
  );
}

function StatedPreferenceForm({
  item,
  mutation,
}: {
  item: InsightItem;
  mutation: MutationHandle;
}) {
  const [topic, setTopic] = useState(
    (item.content.topic as string) ?? "savings",
  );
  const [stance, setStance] = useState((item.content.stance as string) ?? "");
  const existingExtractedAt =
    (item.content.extracted_at as string) ?? new Date().toISOString();
  return (
    <form
      className="space-y-3"
      onSubmit={(event) => {
        event.preventDefault();
        mutation.mutate({
          type: "stated_preference",
          topic,
          stance: stance.trim(),
          raw_quote: stance.trim(),
          extracted_at: existingExtractedAt,
        });
      }}
    >
      <label className="block space-y-1 text-sm">
        <span className="font-medium text-slate-700">Tema</span>
        <select
          className="w-full rounded-md border border-slate-300 px-3 py-2"
          value={topic}
          onChange={(event) => setTopic(event.target.value)}
        >
          {STATED_PREFERENCE_TOPICS.map((value) => (
            <option key={value} value={value}>
              {value}
            </option>
          ))}
        </select>
      </label>
      <label className="block space-y-1 text-sm">
        <span className="font-medium text-slate-700">Tu posición</span>
        <textarea
          className="w-full rounded-md border border-slate-300 px-3 py-2"
          rows={3}
          value={stance}
          onChange={(event) => setStance(event.target.value)}
          required
          minLength={1}
          maxLength={200}
        />
      </label>
      <SubmitRow mutation={mutation} />
    </form>
  );
}

function StatedGoalForm({
  item,
  mutation,
}: {
  item: InsightItem;
  mutation: MutationHandle;
}) {
  const [goalText, setGoalText] = useState(
    (item.content.goal_text as string) ?? "",
  );
  const [status, setStatus] = useState(
    (item.content.status as string) ?? "mentioned",
  );
  const existingTargetAmount = item.content.target_amount as
    | string
    | number
    | undefined;
  const existingTargetDate = item.content.target_date as string | undefined;
  return (
    <form
      className="space-y-3"
      onSubmit={(event) => {
        event.preventDefault();
        const payload: Record<string, unknown> = {
          type: "stated_goal",
          goal_text: goalText.trim(),
          status,
        };
        if (existingTargetAmount !== undefined && existingTargetAmount !== null) {
          payload.target_amount = String(existingTargetAmount);
        }
        if (existingTargetDate) {
          payload.target_date = existingTargetDate;
        }
        mutation.mutate(payload);
      }}
    >
      <label className="block space-y-1 text-sm">
        <span className="font-medium text-slate-700">Meta declarada</span>
        <textarea
          className="w-full rounded-md border border-slate-300 px-3 py-2"
          rows={3}
          value={goalText}
          onChange={(event) => setGoalText(event.target.value)}
          required
          minLength={1}
          maxLength={200}
        />
      </label>
      <fieldset className="space-y-1 text-sm">
        <legend className="font-medium text-slate-700">Estado</legend>
        {STATED_GOAL_STATUSES.map((value) => (
          <label key={value} className="flex items-center gap-2">
            <input
              type="radio"
              name="status"
              value={value}
              checked={status === value}
              onChange={(event) => setStatus(event.target.value)}
            />
            <span>{value}</span>
          </label>
        ))}
      </fieldset>
      <SubmitRow mutation={mutation} />
    </form>
  );
}
