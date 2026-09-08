import { describe, expect, it } from 'vitest';
import { parseResourceEnvironment } from '../../../src/core/resource-env.js';

describe('OTEL_RESOURCE_ATTRIBUTES Python-compatible parsing', () => {
  it.each([
    [undefined, {}],
    ['', {}],
    ['ownerid=00123,instantid=inst-01', { ownerid: '00123', instantid: 'inst-01' }],
    [' ownerid = 01 , instantid = two=three ', { ownerid: '01', instantid: 'two=three' }],
    ['ownerid=a,ownerid=b,empty=', { ownerid: 'b', empty: '' }],
    ['bad,ownerid=1,,instantid=2,broken', { ownerid: '1', instantid: '2' }],
    ['label=%E4%B8%AD%E6%96%87%2C%3D,a%2Eb=x+ y', { label: '中文,=', 'a%2Eb': 'x+ y' }],
    ['x=%25%ZZ%,y=%FF,z=%E4%B8', { x: '%%ZZ%', y: '\uFFFD', z: '\uFFFD' }],
    ['=empty-key', { '': 'empty-key' }],
  ] as const)('parses %s', (raw, expected) => {
    expect(parseResourceEnvironment(raw)).toEqual(expected);
  });

  it('keeps prototype-like names as data without mutating prototypes', () => {
    const attributes = parseResourceEnvironment('__proto__=plain,constructor=id');
    expect(Object.getPrototypeOf(attributes)).toBeNull();
    expect(Object.keys(attributes)).toEqual(['__proto__', 'constructor']);
    expect(attributes.__proto__).toBe('plain');
  });
});
