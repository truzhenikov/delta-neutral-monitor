import test from 'node:test';
import * as assert from 'node:assert/strict';

import { DEFAULT_API_BASE, normalizeApiBase } from './api-base';

test('normalizeApiBase keeps the local default when env is missing', () => {
  assert.equal(normalizeApiBase(undefined), DEFAULT_API_BASE);
  assert.equal(normalizeApiBase('http://127.0.0.1:8080'), 'http://127.0.0.1:8080');
  assert.equal(normalizeApiBase('http://0.0.0.0:8080'), 'http://0.0.0.0:8080');
});

test('normalizeApiBase rewrites IPv4 hosts to sslip.io for Vercel-safe egress', () => {
  assert.equal(normalizeApiBase('http://141.98.85.80:8080'), 'http://141-98-85-80.sslip.io:8080');
  assert.equal(normalizeApiBase('http://141.98.85.80'), 'http://141-98-85-80.sslip.io');
});

test('normalizeApiBase leaves named hosts unchanged', () => {
  assert.equal(normalizeApiBase('http://141-98-85-80.sslip.io'), 'http://141-98-85-80.sslip.io');
  assert.equal(normalizeApiBase('https://api.example.com/base'), 'https://api.example.com/base');
});
