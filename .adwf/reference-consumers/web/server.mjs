import fs from 'node:fs';
import http from 'node:http';
const args = process.argv.slice(2);
const value = (name, fallback) => {
  const index = args.indexOf(name);
  return index >= 0 && args[index + 1] ? args[index + 1] : fallback;
};
const host = value('--host', '127.0.0.1');
const port = Number(value('--port', '4173'));
const body = fs.readFileSync('index.html');
http.createServer((_request, response) => {
  response.writeHead(200, {'content-type': 'text/html; charset=utf-8'});
  response.end(body);
}).listen(port, host);
