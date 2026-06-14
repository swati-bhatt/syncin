import { PrismaClient } from '@prisma/client';

const prisma = new PrismaClient();

// Idempotent: re-running on every container boot just no-ops on the apiKey.
async function main() {
  const tenant = await prisma.tenant.upsert({
    where: { apiKey: 'sk_demo_tenant_key_123' },
    update: {},
    create: {
      name: 'Acme Co',
      timezone: 'America/New_York',
      apiKey: 'sk_demo_tenant_key_123',
    },
  });

  console.log(`✔ seeded tenant "${tenant.name}" (${tenant.id})`);
  console.log(`  API key: ${tenant.apiKey}`);
}

main()
  .catch((err) => {
    console.error(err);
    process.exit(1);
  })
  .finally(() => prisma.$disconnect());
