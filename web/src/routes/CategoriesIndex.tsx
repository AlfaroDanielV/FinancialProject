import { AxiosError } from "axios";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import {
  archiveCategory,
  Category,
  CategoryKind,
  CategoryUpdatePayload,
  createCategory,
  fetchCategories,
  restoreCategory,
  updateCategory,
} from "../api/categories";

const KIND_LABELS: Record<CategoryKind, string> = {
  income: "Ingreso",
  expense: "Gasto",
  both: "Ambos",
};

const KIND_OPTIONS: Array<{ value: CategoryKind; label: string }> = [
  { value: "expense", label: "Gasto" },
  { value: "income", label: "Ingreso" },
  { value: "both", label: "Ambos" },
];

const DEFAULT_NEW_COLOR = "#2563eb";

function apiErrorMessage(error: unknown, fallback: string) {
  if (error instanceof AxiosError) {
    const detail = error.response?.data?.detail;
    if (typeof detail === "string") return detail;
  }
  return fallback;
}

export default function CategoriesIndex() {
  const queryClient = useQueryClient();
  const [showArchived, setShowArchived] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [serverError, setServerError] = useState<string | null>(null);

  const categoriesQuery = useQuery({
    queryKey: ["user-categories", showArchived],
    queryFn: () => fetchCategories(showArchived),
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["user-categories"] });
    queryClient.invalidateQueries({ queryKey: ["transactions"] });
  };

  const createMutation = useMutation({
    mutationFn: createCategory,
    onSuccess: invalidate,
    onError: (err) =>
      setServerError(apiErrorMessage(err, "No pudimos crear la categoría.")),
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: CategoryUpdatePayload }) =>
      updateCategory(id, payload),
    onSuccess: () => {
      invalidate();
      setEditingId(null);
    },
    onError: (err) =>
      setServerError(apiErrorMessage(err, "No pudimos guardar.")),
  });

  const archiveMutation = useMutation({
    mutationFn: (id: string) => archiveCategory(id),
    onSuccess: invalidate,
    onError: (err) =>
      setServerError(apiErrorMessage(err, "No pudimos archivar.")),
  });

  const restoreMutation = useMutation({
    mutationFn: (id: string) => restoreCategory(id),
    onSuccess: invalidate,
    onError: (err) =>
      setServerError(apiErrorMessage(err, "No pudimos restaurar.")),
  });

  const categories = categoriesQuery.data ?? [];
  const sorted = useMemo(() => {
    return [...categories].sort((a, b) => {
      if (a.archived !== b.archived) return a.archived ? 1 : -1;
      return a.name.localeCompare(b.name);
    });
  }, [categories]);

  return (
    <div className="space-y-5">
      <header className="flex flex-col gap-2">
        <Link to="/" className="text-sm font-medium text-accent hover:text-accent-dark">
          Volver al inicio
        </Link>
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">Categorías</h1>
          <p className="mt-1 text-slate-600">
            Categorías default y propias. Renombrar afecta el histórico
            automáticamente (las transacciones guardan el ID, no el texto).
          </p>
        </div>
      </header>

      {serverError && (
        <p className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
          {serverError}
        </p>
      )}

      <NewCategoryForm
        onSubmit={(payload) => createMutation.mutate(payload)}
        submitting={createMutation.isPending}
      />

      <div className="flex items-center justify-between rounded-md border border-slate-200 bg-white px-4 py-3">
        <p className="text-sm text-slate-600">
          {sorted.filter((c) => !c.archived).length} activa
          {sorted.filter((c) => !c.archived).length === 1 ? "" : "s"}
        </p>
        <label className="flex items-center gap-2 text-sm text-slate-700">
          <input
            type="checkbox"
            checked={showArchived}
            onChange={(event) => setShowArchived(event.target.checked)}
          />
          Mostrar archivadas
        </label>
      </div>

      {categoriesQuery.isLoading && (
        <p className="text-slate-500">Cargando categorías...</p>
      )}
      {!categoriesQuery.isLoading && sorted.length === 0 && (
        <div className="rounded-md border border-slate-200 bg-white p-6 text-center text-slate-600">
          <p>Sin categorías todavía.</p>
        </div>
      )}

      <ul className="space-y-2">
        {sorted.map((category) =>
          editingId === category.id ? (
            <CategoryEditRow
              key={category.id}
              category={category}
              onCancel={() => setEditingId(null)}
              onSave={(payload) =>
                updateMutation.mutate({ id: category.id, payload })
              }
              saving={updateMutation.isPending}
            />
          ) : (
            <CategoryRow
              key={category.id}
              category={category}
              onEdit={() => {
                setServerError(null);
                setEditingId(category.id);
              }}
              onArchive={() => {
                if (window.confirm(`Archivar ${category.name}?`)) {
                  archiveMutation.mutate(category.id);
                }
              }}
              onRestore={() => restoreMutation.mutate(category.id)}
            />
          ),
        )}
      </ul>
    </div>
  );
}

function NewCategoryForm({
  onSubmit,
  submitting,
}: {
  onSubmit: (payload: {
    name: string;
    kind: CategoryKind;
    color: string;
    icon?: string;
  }) => void;
  submitting: boolean;
}) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState<CategoryKind>("expense");
  const [color, setColor] = useState(DEFAULT_NEW_COLOR);
  const [icon, setIcon] = useState("");

  return (
    <form
      className="space-y-3 rounded-lg border border-slate-200 bg-white p-4 shadow-sm"
      onSubmit={(event) => {
        event.preventDefault();
        if (name.trim().length < 1) return;
        onSubmit({
          name: name.trim(),
          kind,
          color,
          icon: icon.trim() || undefined,
        });
        setName("");
        setIcon("");
        setColor(DEFAULT_NEW_COLOR);
        setKind("expense");
      }}
    >
      <h2 className="text-base font-semibold text-slate-900">Nueva categoría</h2>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <label className="space-y-1 text-sm">
          <span className="font-medium text-slate-700">Nombre</span>
          <input
            className="w-full rounded-md border border-slate-300 px-3 py-2"
            value={name}
            onChange={(event) => setName(event.target.value)}
            required
            minLength={1}
            maxLength={100}
            placeholder="Marchamo"
          />
        </label>
        <label className="space-y-1 text-sm">
          <span className="font-medium text-slate-700">Tipo</span>
          <select
            className="w-full rounded-md border border-slate-300 px-3 py-2"
            value={kind}
            onChange={(event) => setKind(event.target.value as CategoryKind)}
          >
            {KIND_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </label>
        <label className="space-y-1 text-sm">
          <span className="font-medium text-slate-700">Color</span>
          <input
            type="color"
            className="h-10 w-full rounded-md border border-slate-300"
            value={color}
            onChange={(event) => setColor(event.target.value)}
          />
        </label>
        <label className="space-y-1 text-sm">
          <span className="font-medium text-slate-700">Ícono (opcional)</span>
          <input
            className="w-full rounded-md border border-slate-300 px-3 py-2"
            value={icon}
            onChange={(event) => setIcon(event.target.value)}
            maxLength={64}
            placeholder="car"
          />
        </label>
      </div>
      <button
        type="submit"
        disabled={submitting || name.trim().length < 1}
        className="min-h-11 rounded-md bg-accent px-4 py-2 text-sm font-semibold text-white hover:bg-accent-dark disabled:bg-slate-300"
      >
        {submitting ? "Creando..." : "Crear categoría"}
      </button>
    </form>
  );
}

function CategoryRow({
  category,
  onEdit,
  onArchive,
  onRestore,
}: {
  category: Category;
  onEdit: () => void;
  onArchive: () => void;
  onRestore: () => void;
}) {
  return (
    <li
      className={`flex flex-col gap-3 rounded-md border bg-white p-3 shadow-sm sm:flex-row sm:items-center sm:justify-between ${
        category.archived ? "border-slate-200 opacity-70" : "border-slate-200"
      }`}
    >
      <div className="flex items-center gap-3">
        <span
          className="inline-block h-5 w-5 shrink-0 rounded-full border border-slate-200"
          style={{ backgroundColor: category.color }}
          aria-hidden
        />
        <div>
          <p className="font-medium text-slate-900">
            {category.name}
            {category.is_default && (
              <span className="ml-2 rounded-full bg-blue-100 px-2 py-0.5 text-xs font-medium text-blue-800">
                default
              </span>
            )}
            {category.archived && (
              <span className="ml-2 rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">
                archivada
              </span>
            )}
          </p>
          <p className="text-xs text-slate-500">
            {KIND_LABELS[category.kind as CategoryKind] ?? category.kind} ·{" "}
            {category.transaction_count} transacción
            {category.transaction_count === 1 ? "" : "es"}
            {category.icon ? ` · ${category.icon}` : ""}
          </p>
        </div>
      </div>
      <div className="flex flex-wrap justify-end gap-2">
        {!category.archived && (
          <button
            type="button"
            onClick={onEdit}
            className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50"
          >
            Editar
          </button>
        )}
        {!category.archived &&
          (category.is_default ? (
            <button
              type="button"
              disabled
              title="Las categorías default no se pueden archivar."
              className="min-h-11 cursor-not-allowed rounded-md border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-400"
            >
              Archivar
            </button>
          ) : (
            <button
              type="button"
              onClick={onArchive}
              className="min-h-11 rounded-md border border-red-200 bg-white px-3 py-1.5 text-xs font-semibold text-red-700 hover:bg-red-50"
            >
              Archivar
            </button>
          ))}
        {category.archived && (
          <button
            type="button"
            onClick={onRestore}
            className="min-h-11 rounded-md border border-amber-300 bg-white px-3 py-1.5 text-xs font-semibold text-amber-900 hover:bg-amber-50"
          >
            Restaurar
          </button>
        )}
      </div>
    </li>
  );
}

function CategoryEditRow({
  category,
  onCancel,
  onSave,
  saving,
}: {
  category: Category;
  onCancel: () => void;
  onSave: (payload: CategoryUpdatePayload) => void;
  saving: boolean;
}) {
  const [name, setName] = useState(category.name);
  const [kind, setKind] = useState<CategoryKind>(category.kind as CategoryKind);
  const [color, setColor] = useState(category.color);
  const [icon, setIcon] = useState(category.icon ?? "");

  return (
    <li className="rounded-md border border-slate-200 bg-white p-3 shadow-sm">
      <form
        className="space-y-3"
        onSubmit={(event) => {
          event.preventDefault();
          onSave({
            name: name.trim(),
            kind,
            color,
            icon: icon.trim() || null,
          });
        }}
      >
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          <label className="space-y-1 text-sm">
            <span className="font-medium text-slate-700">Nombre</span>
            <input
              className="w-full rounded-md border border-slate-300 px-3 py-2"
              value={name}
              onChange={(event) => setName(event.target.value)}
              required
              minLength={1}
              maxLength={100}
            />
          </label>
          <label className="space-y-1 text-sm">
            <span className="font-medium text-slate-700">Tipo</span>
            <select
              className="w-full rounded-md border border-slate-300 px-3 py-2"
              value={kind}
              onChange={(event) => setKind(event.target.value as CategoryKind)}
            >
              {KIND_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </label>
          <label className="space-y-1 text-sm">
            <span className="font-medium text-slate-700">Color</span>
            <input
              type="color"
              className="h-10 w-full rounded-md border border-slate-300"
              value={color}
              onChange={(event) => setColor(event.target.value)}
            />
          </label>
          <label className="space-y-1 text-sm">
            <span className="font-medium text-slate-700">Ícono</span>
            <input
              className="w-full rounded-md border border-slate-300 px-3 py-2"
              value={icon}
              onChange={(event) => setIcon(event.target.value)}
              maxLength={64}
            />
          </label>
        </div>
        <div className="flex gap-2">
          <button
            type="submit"
            disabled={saving}
            className="min-h-11 rounded-md bg-accent px-4 py-2 text-sm font-semibold text-white hover:bg-accent-dark disabled:bg-slate-300"
          >
            {saving ? "Guardando..." : "Guardar"}
          </button>
          <button
            type="button"
            onClick={onCancel}
            className="min-h-11 rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50"
          >
            Cancelar
          </button>
        </div>
      </form>
    </li>
  );
}
