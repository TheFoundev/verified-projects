"use strict";

const { createNodeServer } = require("./node");

const port = Number(process.env.PORT || 8080);
const server = createNodeServer();
server.listen(port, () => {
  console.log(`naivecoin listening on http://localhost:${port}`);
});
