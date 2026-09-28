# Prerregistro: criterio de activación de los modelos de precio (Plan v2, B2)

Fijado el 2026-09-28, **antes** de ejecutar el backfill sobre las ~4.6k filas históricas y antes de
que ningún modelo se entrene con el feature store completo. Este archivo no se edita después de ver
resultados; si el criterio resulta mal planteado, se crea una versión nueva con fecha y motivo, no se
reescribe esta.

Transparencia sobre lo ya visto: al diseñar esto se probó una muestra de 633 filas / 250 tokens
(validación agrupada por token). Ahí el modelo dio AUC 0.70 para "tocó +20%" y ~0.56 para "cerró
>= +10%", y **no** cumplía el criterio de abajo (el cuartil superior caía >= 20% más que la base). El
criterio se endureció DESPUÉS de ver eso (comparar contra max(base, 0.5) en vez de contra la base a
secas, porque la base salió por debajo de 0.5 y eso infla la mejora). Se declara para que no se tome
la activación futura como una prueba independiente del diseño.

## Qué se decide
Si `PRICE_MODEL_MODE` pasa de `shadow` a `active` (los candidatos para research profundo se eligen
con los modelos de precio en vez de la heurística volumen/market cap). Lo decide una persona desde el
dashboard; el sistema nunca lo activa solo.

## Definiciones (fijas)
- Features: log10(volumen 24h / market cap), volatilidad horaria de los 7 días previos, momentum 7d.
- Etiqueta primaria: `sustained10` = el retorno al cierre del horizonte (7 días) fue >= +10%.
- Etiqueta de riesgo: `drop20` = el precio cayó >= 20% respecto a la entrada en algún momento.
- Etiqueta histórica (solo comparación): `touch20` = tocó +20% en algún momento.
- Validación: 100 particiones aleatorias AGRUPADAS POR TOKEN (30% de los tokens fuera de
  entrenamiento en cada una), más una prueba fuera de tiempo (entrenar con el 70% más antiguo,
  probar en el 30% más reciente).
- Línea base: regresión con solo log10(volumen/market cap). La mejora se mide contra max(AUC base, 0.5).

## Criterio de activación (deben cumplirse TODOS, sobre `sustained10`)
1. Al menos 200 tokens distintos con resultado y features.
2. Mejora media del AUC sobre max(base, 0.5) >= +0.03.
3. El límite inferior del IC95 de esa mejora (por token) es > 0.
4. El cuartil superior por puntaje del modelo NO cae >= 20% con más frecuencia que la base de la
   muestra de prueba.
5. En la prueba fuera de tiempo, la mejora sobre max(base, 0.5) es > 0.

`price_models.activation_verdict()` implementa exactamente esto y `retrain_model.py` lo guarda con
cada entrenamiento (visible en el dashboard). Cumplirlo es condición necesaria, no obligación de
activar.

## Regla de ranking cuando esté activo
Excluir el cuartil de mayor probabilidad de `drop20` entre los sobrevivientes del ciclo y ordenar el
resto por probabilidad de `sustained10` (`price_models.rank_by_price_model`).

## Qué NO se vale
- Cambiar features, etiquetas, umbrales o particiones después de ver resultados para que el criterio
  se cumpla.
- Activar por un solo entrenamiento afortunado: el criterio debe cumplirse en al menos 2
  reentrenamientos semanales consecutivos.
