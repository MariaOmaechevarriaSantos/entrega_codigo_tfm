# 7. Datos en un volumen con nombre, no horneados en la imagen

- Estado: aceptada
- Fecha: 2026-08-30
- Fase: P5 (bloque 8)

## Contexto

La API necesita `data/processed/` (callejero, modelo, DuckDB reducida,
isócronas precomputadas, GeoJSON de equipamientos). Se podían copiar esos
ficheros dentro de la imagen en el `docker build`. Requisito explícito del
autor: regenerar el callejero o reentrenar el modelo **no** debe obligar a
reconstruir imágenes, para que el entorno del autor y el del tribunal
sigan siendo el mismo.

## Decisión

Los datos viajan en un `artifacts.tar.gz` (local en `./dist` o de un
GitHub Release) y un servicio efímero `data-init` puebla con ellos un
**volumen con nombre** (`processed_data`), verificándolos fichero a fichero
contra `artifacts.manifest.json` (existe + bytes + sha256). La API monta
ese volumen en `/app/data/processed`. Las imágenes **no** contienen datos
(`.dockerignore` excluye `data/`).

## Consecuencias

- Regenerar un artefacto = repoblar el volumen (`docker compose down -v` +
  `up`), no `docker build`. Los arranques siguientes no descargan nada:
  `data-init` ve el volumen ya verificado y sale sin tocar nada.
- El estado "datos listos" es observable (`docker compose ps`, logs de
  `data-init`), no está incrustado en el arranque de la API.
- El manifiesto se versiona en el repo; el tarball vive en el Release →
  orígenes independientes, la verificación no es circular.
- Contrapartida: hay un artefacto más que mantener (el tarball + su
  manifiesto) y un primer arranque que extrae ~5 MB.

## Alternativas descartadas

- **Datos horneados en la imagen**: ata el ciclo de vida de los datos al
  de la imagen; cualquier cambio de datos obliga a rebuild y rompe la
  paridad autor/tribunal.
- **Manifiesto que solo guarda el sha del tarball**: si tarball y
  manifiesto se generan en el mismo paso, verificar sería comparar el
  fichero consigo mismo. El manifiesto lista el sha256 de **cada** fichero
  extraído.
