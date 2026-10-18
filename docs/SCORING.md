# Semántica inicial

Todos los niveles del modo actual describen anomalías de Telcel. No prueban utilidad, liquidez, error del comercio ni precio de reventa.

Los umbrales iniciales son decisiones de implementación configurables, independientes de la maqueta:

| Nivel | Caída observada | Evidencia adicional |
|---|---:|---|
| WATCH | <15% o sin base propia | Se inicia memoria |
| B | ≥15% | Mismo SKU observado previamente |
| A | ≥30% | Mismo SKU observado previamente |
| S+ | ≥50% | ≥24 h de historia, ≥6 observaciones, precio exacto y disponibilidad en ficha |
| S++ | ≥80% | Lo anterior y ≥7 días distintos de referencia |

Referencia: mediana conservada por día de los últimos 14 días, excluyendo el día actual; si aún no existe, última observación. El precio anterior publicado se muestra pero no entra en la base. El criterio debe calibrarse con datos reales; no se ha realizado backtesting de rentabilidad.

Realerta: mejora de al menos 3% frente al precio de la última alerta enviada, aumento de nivel, nueva reaparición o aumento de evidencia. Por defecto ntfy sólo envía S+ y S++. El filtro local de interfaz no cambia silenciosamente el emisor del servidor.

La ventana caliente se calcula en `America/Mexico_City`. La programación usa los horarios UTC correspondientes; el guard utiliza la hora real de ejecución para tolerar retrasos.
