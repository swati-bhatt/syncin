// Domain errors carry an HTTP status + a stable machine code. The Fastify
// error handler (src/app.ts) maps any AppError to a clean JSON response, so
// services can throw without knowing anything about HTTP.
export class AppError extends Error {
  readonly statusCode: number;
  readonly code: string;

  constructor(statusCode: number, code: string, message: string) {
    super(message);
    this.statusCode = statusCode;
    this.code = code;
  }
}

export class BadRequest extends AppError {
  constructor(message: string) {
    super(400, 'bad_request', message);
  }
}

export class Unauthorized extends AppError {
  constructor(message = 'Unauthorized') {
    super(401, 'unauthorized', message);
  }
}

export class UnprocessableEntity extends AppError {
  constructor(message: string) {
    super(422, 'unprocessable_entity', message);
  }
}
