// Modelence Cloud runs Node only, so this thin edge forwards every request
// (including the server-sent evolution stream) to the Python API at FASTAPI_URL.
const http = require("http");
const https = require("https");

const fs = require("fs");
// FASTAPI_URL wins; otherwise upstream.txt (written at deploy time, e.g. the tunnel URL).
const fallback = fs.existsSync(__dirname + "/upstream.txt") ? fs.readFileSync(__dirname + "/upstream.txt", "utf8").trim() : "";
const target = new URL(process.env.FASTAPI_URL || fallback || "http://127.0.0.1:8000");
const client = target.protocol === "https:" ? https : http;
const port = process.env.PORT || 3000;

http.createServer((req, res) => {
  if (req.url === "/healthz") return res.end("ok");
  const upstream = client.request(
    {
      hostname: target.hostname,
      port: target.port || (target.protocol === "https:" ? 443 : 80),
      path: req.url,
      method: req.method,
      headers: { ...req.headers, host: target.host },
    },
    (up) => {
      res.writeHead(up.statusCode, up.headers);
      up.pipe(res);
    }
  );
  upstream.on("error", (e) => {
    res.writeHead(502, { "content-type": "text/plain" });
    res.end("upstream unavailable: " + e.message);
  });
  req.pipe(upstream);
}).listen(port, () => console.log(`omniq-atlas edge on :${port} -> ${target.origin}`));
