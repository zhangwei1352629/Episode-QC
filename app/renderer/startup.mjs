export async function loadInitialTasks({ getTasks, refreshNasStatus }) {
  void Promise.resolve()
    .then(refreshNasStatus)
    .catch(() => undefined);
  return getTasks();
}
