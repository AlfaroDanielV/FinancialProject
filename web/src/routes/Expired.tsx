export default function Expired() {
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold text-slate-900">Tu link expiró</h1>
      <p className="text-slate-700">
        Los links del setup duran 30 minutos y se usan una sola vez. Volvé a
        Telegram y mandale <code className="bg-slate-100 px-1 rounded">/setup</code>{" "}
        al bot para recibir uno nuevo.
      </p>
      <p className="text-sm text-slate-500">
        Si ya estabas adentro y te apareció esta pantalla, tu sesión de 4 horas
        terminó. Pedí otro link y seguís donde quedaste.
      </p>
    </div>
  );
}
