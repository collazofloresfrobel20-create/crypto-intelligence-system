/**
 * El botón "Actualizar sistema" ya no corre el pipeline dentro del Worker (no puede: un
 * ciclo real tarda 10-60 min por el rate limit gratuito de Gemini, muy por encima del límite
 * de ejecución de un Worker). En vez de eso, dispara el workflow de GitHub Actions que hace
 * el trabajo pesado, vía la API REST de GitHub.
 */
export async function dispatchUpdateWorkflow(env) {
  const [owner, repo] = (env.GITHUB_REPO || "").split("/");
  if (!owner || !repo || !env.GITHUB_PAT) {
    throw new Error(
      "Falta configurar GITHUB_REPO y/o GITHUB_PAT como secretos del Worker."
    );
  }
  const workflowFile = env.GITHUB_WORKFLOW_FILE || "update-cycle.yml";
  const url = `https://api.github.com/repos/${owner}/${repo}/actions/workflows/${workflowFile}/dispatches`;

  const resp = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_PAT}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "cis-dashboard-worker",
    },
    body: JSON.stringify({ ref: env.GITHUB_BRANCH || "main" }),
  });

  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`GitHub API respondió ${resp.status}: ${text}`);
  }
}

/** Corridas recientes del workflow, para mostrar estado/progreso en el dashboard. */
export async function getRecentWorkflowRuns(env, limit = 5) {
  const [owner, repo] = (env.GITHUB_REPO || "").split("/");
  if (!owner || !repo || !env.GITHUB_PAT) return [];
  const workflowFile = env.GITHUB_WORKFLOW_FILE || "update-cycle.yml";
  const url = `https://api.github.com/repos/${owner}/${repo}/actions/workflows/${workflowFile}/runs?per_page=${limit}`;

  const resp = await fetch(url, {
    headers: {
      Authorization: `Bearer ${env.GITHUB_PAT}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "cis-dashboard-worker",
    },
  });
  if (!resp.ok) return [];
  const data = await resp.json();
  return (data.workflow_runs || []).map((r) => ({
    id: r.id,
    status: r.status,
    conclusion: r.conclusion,
    created_at: r.created_at,
    html_url: r.html_url,
  }));
}
