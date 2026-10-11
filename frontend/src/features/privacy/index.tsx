import { Link } from "react-router-dom";

const SECTIONS: { title: string; body: string[] }[] = [
  {
    title: "Qué datos tratamos",
    body: [
      "El sistema detecta equipos de protección personal con una cámara. Cada vez que confirma una detección guarda una captura de la imagen, que puede mostrar a trabajadores, junto con la clase detectada, la hora y la confianza del modelo.",
      "También registramos quién entra al panel y qué acciones realiza (por ejemplo, ver una captura original o pausar la detección).",
    ],
  },
  {
    title: "Para qué los usamos",
    body: [
      "Verificar el uso de equipos de protección, auditar cada evento y mejorar el modelo de detección. No se usan para otro fin ni se publican.",
    ],
  },
  {
    title: "Base legal",
    body: [
      "El tratamiento se realiza conforme a la Ley N.º 29733, Ley de Protección de Datos Personales del Perú, y su reglamento.",
    ],
  },
  {
    title: "Cuánto tiempo se conservan",
    body: [
      "Las imágenes se eliminan automáticamente a los 60 días. Los datos del evento (clase, hora, duración y confianza) no incluyen imágenes y se conservan sin límite para los reportes.",
    ],
  },
  {
    title: "Quién puede verlos",
    body: [
      "Solo las personas autorizadas por el administrador, con una cuenta de Google. Hay tres roles: observador (solo lectura), operador (puede además pausar la detección) y administrador (gestiona usuarios y revisa la auditoría). Las descargas de imágenes originales quedan registradas.",
    ],
  },
  {
    title: "Tus derechos",
    body: [
      "Puedes pedir acceso, rectificación o eliminación de tus datos al administrador del sistema. Para eliminar una captura se borra el objeto asociado a su identificador de evento.",
    ],
  },
  {
    title: "Limitación conocida",
    body: [
      "Las imágenes no se difuminan: los rostros pueden verse en las capturas. Se considera una mejora futura; mientras tanto el acceso está restringido y auditado.",
    ],
  },
];

/** Página pública /privacidad. Resume el contenido de docs/PRIVACY.md en lenguaje claro. */
export default function PrivacyPage() {
  return (
    <main className="mx-auto min-h-dvh max-w-2xl bg-page px-4 py-8">
      <article aria-labelledby="titulo">
        <h1 id="titulo" className="text-2xl font-semibold text-ink">
          Aviso de privacidad
        </h1>
        <p className="mt-2 text-sm text-ink-2">
          EPP Monitor · monitoreo de equipos de protección personal
        </p>
        {SECTIONS.map((s) => (
          <section key={s.title} className="mt-6">
            <h2 className="text-xl font-semibold text-ink">{s.title}</h2>
            {s.body.map((p) => (
              <p key={p} className="mt-2 text-base text-ink-2">
                {p}
              </p>
            ))}
          </section>
        ))}
        <p className="mt-8">
          <Link to="/login" className="text-accent-text underline">
            Volver al inicio de sesión
          </Link>
        </p>
      </article>
    </main>
  );
}
