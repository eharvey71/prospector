// Runs on the Job Engine web app only. The queue page posts an autofill
// payload via window.postMessage; this forwards it into extension storage
// and acks so the page knows the extension is installed.
window.addEventListener("message", (ev) => {
  if (ev.source !== window) return;
  if (ev.data?.type !== "JOB_ENGINE_AUTOFILL") return;
  chrome.runtime.sendMessage({ kind: "store", payload: ev.data.payload }, () => {
    window.postMessage({ type: "JOB_ENGINE_AUTOFILL_ACK" }, "*");
  });
});
