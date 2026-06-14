// Fastify JSON-schema validation for POST /v1/events. Invalid bodies are
// rejected with a 400 before any handler code runs.
export const createEventSchema = {
  body: {
    type: 'object',
    required: ['recipient', 'startsAt'],
    additionalProperties: false,
    properties: {
      recipient: {
        type: 'object',
        required: ['name', 'phone'],
        additionalProperties: false,
        properties: {
          name: { type: 'string', minLength: 1, maxLength: 200 },
          phone: { type: 'string', pattern: '^\\+[1-9]\\d{6,14}$' }, // E.164
        },
      },
      startsAt: { type: 'string', minLength: 1 }, // ISO-8601; validity checked in the service
      notes: { type: 'string', maxLength: 1000 },
      memberId: { type: 'string', format: 'uuid' },
    },
  },
  headers: {
    type: 'object',
    properties: {
      'idempotency-key': { type: 'string', maxLength: 255 },
    },
  },
} as const;
