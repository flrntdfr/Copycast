/** The MSW server shared by every test; tests add handlers with `server.use(...)`. */
import { setupServer } from "msw/node";

export const server = setupServer();
