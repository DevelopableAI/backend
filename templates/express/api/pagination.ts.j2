import { Request } from 'express';

export interface PaginationParams {
  page: number;
  limit: number;
  skip: number;
}

export interface PaginatedResponse<T> {
  data: T[];
  meta: {
    total: number;
    page: number;
    limit: number;
    totalPages: number;
    hasNext: boolean;
    hasPrev: boolean;
  };
}

export function parsePagination(req: Request): PaginationParams {
  const page = Math.max(1, parseInt(req.query.page as string) || 1);
  const limit = Math.min(100, Math.max(1, parseInt(req.query.limit as string) || 20));
  const skip = (page - 1) * limit;
  return { page, limit, skip };
}

export function buildPaginatedResponse<T>(
  data: T[],
  total: number,
  params: PaginationParams,
): PaginatedResponse<T> {
  const totalPages = Math.ceil(total / params.limit);
  return {
    data,
    meta: {
      total,
      page: params.page,
      limit: params.limit,
      totalPages,
      hasNext: params.page < totalPages,
      hasPrev: params.page > 1,
    },
  };
}

export interface ListQueryParams extends PaginationParams {
  sort?: { field: string; order: 'asc' | 'desc' };
  filters: Record<string, string>;
}

export function parseListQuery(
  req: Request,
  allowedFilterFields: readonly string[],
  allowedSortFields: readonly string[],
): ListQueryParams {
  const pagination = parsePagination(req);

  let sort: { field: string; order: 'asc' | 'desc' } | undefined;
  const sortBy = typeof req.query.sortBy === 'string' ? req.query.sortBy : undefined;
  if (sortBy && allowedSortFields.includes(sortBy)) {
    const sortOrder = req.query.sortOrder === 'desc' ? 'desc' : 'asc';
    sort = { field: sortBy, order: sortOrder };
  }

  const filters: Record<string, string> = {};
  for (const field of allowedFilterFields) {
    const val = req.query[field];
    if (typeof val === 'string') filters[field] = val;
  }

  return { ...pagination, sort, filters };
}
