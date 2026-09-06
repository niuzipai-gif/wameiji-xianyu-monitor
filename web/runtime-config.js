// Deployment-time browser configuration.  Keep this file free of secrets.
// Set apiBase to the public collector API origin when the UI is hosted on a
// different origin (GitHub Pages -> Render).  A URL ?api_base=... override or
// localStorage key cd_monitor_api_base takes precedence at runtime.
window.CD_MONITOR_CONFIG = window.CD_MONITOR_CONFIG || {
  apiBase: "",
  accessToken: "",
};
