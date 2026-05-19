type CalculateLiqDistancePctInput = {
  side: 'long' | 'short';
  markPrice: number;
  liquidationPrice: number | null;
};

export function calculateLiqDistancePct({ side, markPrice, liquidationPrice }: CalculateLiqDistancePctInput): number | null {
  if (liquidationPrice === null || markPrice <= 0) {
    return null;
  }

  const distance = side === 'long'
    ? ((markPrice - liquidationPrice) / markPrice) * 100
    : ((liquidationPrice - markPrice) / markPrice) * 100;

  return Number(distance.toFixed(2));
}
