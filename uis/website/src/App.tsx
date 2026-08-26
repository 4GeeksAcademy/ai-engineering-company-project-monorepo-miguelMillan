import { ContactForm } from './components/ContactForm'
import { SectionTitle } from './components/SectionTitle'
import { ServiceCard } from './components/ServiceCard'

const services = [
  {
    title: 'Orquestacion de inventario multi-almacen',
    summary:
      'Unificamos stock de Los Angeles y Zaragoza para que tu equipo comercial y operaciones trabajen con una sola verdad operativa.',
    stat: '2 paises, 1 vista de inventario',
  },
  {
    title: 'Seleccion inteligente de transportistas',
    summary:
      'Asignamos cada envio al carrier mas conveniente por coste, distancia y urgencia, minimizando incidencias de ultima milla.',
    stat: '8 carriers integrables',
  },
  {
    title: 'Gestion de devoluciones basada en reglas',
    summary:
      'Convertimos devoluciones manuales en flujos trazables con criterios por cliente, reduciendo tiempos de aprobacion y reprocesos.',
    stat: '18-25% del volumen optimizable',
  },
]

const team = [
  { name: 'Ana Whitfield', role: 'Directora de Operaciones de Almacen' },
  { name: 'Carlos Vega', role: 'Responsable de Last Mile y Carriers' },
  { name: 'Sofia Ramos', role: 'Lider de Logistica Inversa' },
  { name: 'Valentina Cruz', role: 'Manager de Customer Experience' },
]

function App() {
  return (
    <div className="min-h-screen bg-gradient-to-b from-cyan-50 via-white to-slate-100 text-slate-900">
      <header className="border-b border-cyan-100 bg-white/80 backdrop-blur-sm">
        <nav
          className="mx-auto flex w-full max-w-6xl items-center justify-between px-6 py-5"
          aria-label="Navegacion principal"
        >
          <a href="#inicio" className="font-display text-2xl font-bold tracking-tight text-slate-900">
            TrackFlow
          </a>
          <ul className="flex gap-5 text-sm font-semibold text-slate-700">
            <li>
              <a className="hover:text-cyan-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan-700" href="#servicios">
                Servicios
              </a>
            </li>
            <li>
              <a className="hover:text-cyan-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan-700" href="#equipo">
                Equipo
              </a>
            </li>
            <li>
              <a className="hover:text-cyan-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan-700" href="#contacto">
                Contacto
              </a>
            </li>
          </ul>
        </nav>
      </header>

      <main id="inicio">
        <section className="mx-auto grid w-full max-w-6xl gap-10 px-6 py-16 lg:grid-cols-[1.2fr_0.8fr] lg:items-center">
          <article>
            <p className="text-sm font-semibold uppercase tracking-[0.2em] text-cyan-700">
              Logistica inteligente para e-commerce
            </p>
            <h1 className="mt-4 font-display text-4xl font-extrabold leading-tight text-slate-900 md:text-6xl">
              Operaciones de ultima milla que escalan contigo
            </h1>
            <p className="mt-6 max-w-2xl text-lg text-slate-600">
              Desde 2009 ayudamos a marcas en Estados Unidos y Espana a convertir su cadena logistica en una ventaja competitiva: inventario confiable, envios trazables y devoluciones eficientes.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <a
                href="#contacto"
                className="rounded-xl bg-slate-900 px-5 py-3 text-sm font-semibold text-white transition hover:bg-slate-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900"
              >
                Solicitar diagnostico operativo
              </a>
              <a
                href="#servicios"
                className="rounded-xl border border-slate-300 bg-white px-5 py-3 text-sm font-semibold text-slate-700 transition hover:bg-slate-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900"
              >
                Ver capacidades
              </a>
            </div>
          </article>

          <aside className="rounded-3xl border border-cyan-100 bg-white p-8 shadow-lg">
            <h2 className="font-display text-2xl font-bold text-slate-900">Cobertura operacional</h2>
            <dl className="mt-6 space-y-4">
              <div className="flex items-center justify-between border-b border-slate-200 pb-3">
                <dt className="text-slate-600">Almacenes activos</dt>
                <dd className="font-semibold text-slate-900">Los Angeles + Zaragoza</dd>
              </div>
              <div className="flex items-center justify-between border-b border-slate-200 pb-3">
                <dt className="text-slate-600">Transportistas coordinados</dt>
                <dd className="font-semibold text-slate-900">8 partners</dd>
              </div>
              <div className="flex items-center justify-between border-b border-slate-200 pb-3">
                <dt className="text-slate-600">Equipo total</dt>
                <dd className="font-semibold text-slate-900">~130 personas</dd>
              </div>
              <div className="flex items-center justify-between">
                <dt className="text-slate-600">Ingresos anuales</dt>
                <dd className="font-semibold text-slate-900">9M EUR</dd>
              </div>
            </dl>
          </aside>
        </section>

        <section id="mision" className="mx-auto w-full max-w-6xl px-6 py-10">
          <div className="grid gap-6 md:grid-cols-2">
            <article className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
              <h2 className="font-display text-2xl font-bold text-slate-900">Mision</h2>
              <p className="mt-3 text-slate-600">
                Resolver la complejidad operativa de la logistica e-commerce para que nuestros clientes puedan enfocarse en crecer su negocio.
              </p>
            </article>
            <article className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
              <h2 className="font-display text-2xl font-bold text-slate-900">Vision</h2>
              <p className="mt-3 text-slate-600">
                Ser la plataforma de referencia en operaciones logisticas inteligentes para empresas que venden en multiples mercados.
              </p>
            </article>
          </div>
        </section>

        <section id="servicios" className="mx-auto w-full max-w-6xl px-6 py-12">
          <SectionTitle
            eyebrow="Servicios"
            title="Soluciones construidas para operaciones reales"
            description="Disenamos procesos y tecnologia para mejorar visibilidad, coste y calidad de entrega en toda la cadena de fulfillment."
          />
          <div className="mt-10 grid gap-6 md:grid-cols-2 lg:grid-cols-3">
            {services.map((service) => (
              <ServiceCard
                key={service.title}
                title={service.title}
                summary={service.summary}
                stat={service.stat}
              />
            ))}
          </div>
        </section>

        <section id="equipo" className="mx-auto w-full max-w-6xl px-6 py-12">
          <SectionTitle
            eyebrow="Equipo"
            title="Expertos en almacen, ultima milla y experiencia cliente"
            description="Nuestro liderazgo combina experiencia operativa en dos paises con una hoja de ruta de transformacion digital continua."
          />
          <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {team.map((person) => (
              <article key={person.name} className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
                <h3 className="font-display text-xl font-semibold text-slate-900">{person.name}</h3>
                <p className="mt-2 text-sm text-slate-600">{person.role}</p>
              </article>
            ))}
          </div>
        </section>

        <section id="contacto" className="mx-auto w-full max-w-4xl px-6 py-12">
          <SectionTitle
            eyebrow="Contacto"
            title="Cuéntanos tu reto logistico"
            description="Comparte tu operacion actual y te devolveremos una propuesta concreta para mejorar servicio y rentabilidad."
          />
          <div className="mt-8 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm md:p-8">
            <ContactForm />
          </div>
        </section>
      </main>

      <footer className="border-t border-slate-200 bg-white py-8">
        <div className="mx-auto flex w-full max-w-6xl flex-col gap-2 px-6 text-sm text-slate-600 md:flex-row md:items-center md:justify-between">
          <p>TrackFlow Tech · Los Angeles + Zaragoza</p>
          <p>Ultima milla, inventario y devoluciones con enfoque data-driven.</p>
        </div>
      </footer>
    </div>
  )
}

export default App
