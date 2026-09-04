import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

/**
 * RETIRED — the model is no longer trained from this app.
 *
 * Training moved to a Colab notebook, which exports the artifacts in
 * `model/artifacts/`. The model server now only serves them; it has no
 * `/retrain` endpoint to call, and the v2 `pipeline.py` / `train.py` this route
 * depended on have been removed.
 *
 * To refresh the model:
 *   1. cd data && python pipeline.py --with-api   (rebuild model_data.csv)
 *   2. run the Colab notebook on it
 *   3. drop the exported artifacts into model/artifacts/ and restart the server
 */
export async function POST() {
  return NextResponse.json(
    {
      error: 'Retired endpoint',
      message: 'The model is trained in Colab, not from the web app.',
      how_to_retrain: [
        'cd data && python pipeline.py --with-api',
        'run the Colab training notebook on data/output/model_data.csv',
        'copy the exported artifacts into model/artifacts/ and restart the model server',
      ],
    },
    { status: 410 }
  );
}

export async function GET() {
  return POST();
}
