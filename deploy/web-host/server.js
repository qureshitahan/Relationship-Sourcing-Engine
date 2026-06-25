/**
 * Serves the Vite production build on Azure App Service (Node).
 * PORT is set automatically by App Service.
 */
const express = require("express");
const path = require("path");

const app = express();
const port = process.env.PORT || 8080;
const dist = path.join(__dirname, "dist");

app.use(express.static(dist));

// SPA fallback — React Router paths
app.get("*", (_req, res) => {
  res.sendFile(path.join(dist, "index.html"));
});

app.listen(port, () => {
  console.log(`Web app listening on port ${port}`);
});
