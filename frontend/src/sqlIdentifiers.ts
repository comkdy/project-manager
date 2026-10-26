// PostgreSQL keywords that SQLAlchemy's identifier preparer also quotes.
const reserved=new Set(`all analyse analyze and any array as asc asymmetric authorization between binary both case cast check collate column constraint create cross current_catalog current_date current_role current_schema current_time current_timestamp current_user default deferrable desc distinct do else end except false fetch for foreign freeze from full grant group having ilike in initially inner intersect into is isnull join leading left like limit localtime localtimestamp natural new not notnull null of off offset old on only or order outer over overlaps placing primary references returning right select session_user similar some symmetric table then to trailing true union unique user using variadic verbose when where window with`.split(' '));

export function columnExpression(name:string):string{
 return /^[a-z_][a-z0-9_]*$/.test(name)&&!reserved.has(name)?name:`"${name.replace(/"/g,'""')}"`;
}

// Canonical expressions quote every identifier. Make ordinary names readable
// without changing string literals, escaped strings, or dollar-quoted values.
export function displayExpression(expression:string):string{
 return expression.replace(/\b[Ee]'(?:\\[\s\S]|''|[^'])*'|'(?:''|[^'])*'|\$([A-Za-z_][A-Za-z0-9_]*|)\$[\s\S]*?\$\1\$|"(?:[^"]|"")*"/g,token=>
  token.startsWith('"')?columnExpression(token.slice(1,-1).replace(/""/g,'"')):token);
}
