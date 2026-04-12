# RailMan AI — MongoDB Schema

## Collection: `stations`
| Field  | Type    | Description                            |
|--------|---------|----------------------------------------|
| id     | string  | Short code e.g. "CG", "BV"            |
| name   | string  | Full station name                      |
| lat    | float   | GPS latitude                           |
| lng    | float   | GPS longitude                          |
| zone   | string  | south / central / north / far-north    |
| index  | int     | Position on line (0 = Churchgate)      |
| loc    | GeoJSON | { type: "Point", coordinates: [lng, lat] } |

## Collection: `trains`
| Field            | Type    | Description                         |
|------------------|---------|-------------------------------------|
| id               | string  | e.g. "WR-101"                       |
| name             | string  | Human-readable name                 |
| type             | string  | "fast" or "slow"                    |
| origin           | string  | Station id                          |
| terminus         | string  | Station id                          |
| direction        | int     | 1 = towards Virar, -1 = Churchgate  |
| stops            | array   | List of stop ids, or "all"          |
| color            | string  | Hex colour for map marker           |

## Collection: `logs`
| Field      | Type     | Description                         |
|------------|----------|-------------------------------------|
| session_id | string   | UUID per app session                |
| message    | string   | User's query                        |
| response   | object   | Full API response                   |
| timestamp  | datetime | UTC                                 |

## Collection: `recommendations`
| Field          | Type     | Description            |
|----------------|----------|------------------------|
| session_id     | string   |                        |
| request        | object   | Source, dest, pref     |
| recommendation | object   | Best + alternatives    |
| timestamp      | datetime |                        |

## Collection: `feedback`
| Field      | Type     | Description       |
|------------|----------|-------------------|
| session_id | string   |                   |
| rating     | int      | 1–5               |
| comment    | string   | Optional          |
| timestamp  | datetime |                   |
