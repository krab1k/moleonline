import { readFileSync } from 'node:fs';
import openapiTS from 'openapi-typescript';
import { describe, expect, it } from 'vitest';

// MoleAPISchema.d.ts is generated from the API's description, api/openapi.json (kept current by the API's tests)
describe('MoleAPISchema.d.ts', () => {
    it("matches the API's OpenAPI description (npm run api-types)", async () => {
        const description = JSON.parse(readFileSync(new URL('../../../api/openapi.json', import.meta.url), 'utf8'));
        const generated = await openapiTS(description);
        expect(readFileSync(new URL('./MoleAPISchema.d.ts', import.meta.url), 'utf8')).toBe(generated);
    });
});
