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

Las alertas personales son independientes de esos niveles. `alert_rules.json` activa:

- Celular completo por **menos de $1,800 MXN**, incluida su primera observación.
- Promoción publicada de **al menos 50% y $1,000 de ahorro**. El precio tachado es evidencia publicitaria, nunca base de caída propia ni ROI.
- Caída observada de **al menos 30%** frente al histórico propio, además de S+ y S++.

Cada oportunidad reúne sus motivos en un solo aviso por SKU, precio y episodio. Precio exacto en ficha y disponibilidad observada permiten marcar CONFIRMADA; los demás avisos se rotulan PRELIMINAR. Los confirmados debajo del precio objetivo y S++ usan prioridad 5; otros avisos personales, 4. No se notifican cuotas, enganches, productos agotados ni observaciones mayores de 30 minutos.

Realerta: mejora de al menos 3% frente al precio de la última alerta enviada, nuevo criterio cumplido, aumento de nivel, nueva reaparición o paso de preliminar a confirmada. Una degradación a preliminar no vuelve a notificar. El filtro histórico local no silencia promociones fuertes ni el precio objetivo, y no modifica el emisor.

La cola se guarda y se confirma en Git antes de contactar ntfy. Los fallos mantienen el aviso pendiente con espera creciente, y se descartan precios obsoletos, agotados o vencidos. Las confirmaciones se guardan después de cada respuesta y se suben incluso si falla un envío. Una interrupción entre la aceptación externa y el guardado remoto todavía puede duplicar un aviso; no se promete entrega exactamente una vez. “Aceptado por ntfy” no confirma recepción en un teléfono.

La ventana caliente se calcula en `America/Mexico_City`. La programación usa los horarios UTC correspondientes; el guard utiliza la hora real de ejecución para tolerar retrasos.

Ventanas: **02:00–04:59 y 14:00–17:59 CDMX**, objetivo de 5 minutos; fuera de ellas, 15 minutos. GitHub puede retrasar o perder disparos programados. En horario intensivo se priorizan celulares, se mantiene el cursor de otras categorías y se limita el barrido a 150 segundos. Las fichas de candidatos se verifican antes de las otras superficies y los SKUs conocidos se vigilan aunque la paginación esté en otra página. Una comprobación de ficha puede reutilizarse durante menos de 10 minutos si el catálogo vuelve a mostrar el mismo precio; su hora se conserva y se muestra.

Las pruebas del motor y la integridad de datos se ejecutan siempre. Las pruebas visuales completas y de la PWA pública se ejecutan al cambiar código o solicitar una ejecución manual, sin retrasar cada barrido programado.
