import jwt from 'jsonwebtoken';
import { ITokenService } from '../contracts/token-service.contract.js';

export class JwtTokenService implements ITokenService {
  async sign(payload: Record<string, unknown>): Promise<string> {
    return jwt.sign(payload, process.env.JWT_SECRET!, { expiresIn: '7d' });
  }

  async verify<T extends Record<string, unknown>>(token: string): Promise<T> {
    return jwt.verify(token, process.env.JWT_SECRET!) as T;
  }
}
