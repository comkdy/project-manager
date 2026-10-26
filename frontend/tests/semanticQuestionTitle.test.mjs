import assert from 'node:assert/strict';
import test from 'node:test';
import {questionTitle,questionTitleParts} from '../src/semanticQuestionTitle.ts';

const column=name=>({kind:'column',column:name,source_id:'',value:''});

test('column labels become part of the question without changing identifiers',()=>{
 const question={title:'passed는 어떤 기준으로 판정되며 bin 값과 어떤 관계인가요?',targets:[column('passed'),column('bin')]};
 assert.equal(questionTitle(question),'passed 컬럼은 어떤 기준으로 판정되며, bin 컬럼의 값과는 어떤 관계인가요?');
 assert.equal(questionTitle({...question,title:questionTitle(question)}),questionTitle(question));
});

test('existing labels, unknown terms, literal values and longer identifiers stay intact',()=>{
 const title="passed 컬럼과 bin 컬럼, 'passed', passed_count의 차이는 무엇인가요?";
 assert.equal(questionTitle({title,targets:[column('passed'),column('bin')]}),title);
 assert.equal(questionTitle({title:'미정 용어는 무엇인가요?',targets:[]}),'미정 용어는 무엇인가요?');
});

test('explicit values retain their column context in the sentence',()=>{
 const targets=[{...column('quality'),kind:'column_value',value:'PASS'}];
 assert.equal(questionTitle({title:"quality의 'PASS'는 무슨 뜻인가요?",targets}),"quality 컬럼의 값 'PASS'는 무슨 뜻인가요?");
 assert.equal(questionTitle({title:"'PASS'는 무슨 뜻인가요?",targets}),"quality 컬럼의 값 'PASS'는 무슨 뜻인가요?");
 const title="quality 컬럼의 'PASS' 값은 무슨 뜻인가요?";
 assert.equal(questionTitle({title,targets}),title);
 assert.equal(questionTitle({title:"quality 컬럼의 'PASS-OTHER' 값은 무슨 뜻인가요?",targets}),"quality 컬럼의 'PASS-OTHER' 값은 무슨 뜻인가요?");
});

test('legacy prefixes and Korean particles become natural suffix labels',()=>{
 const targets=[column('passed'),column('bin')];
 assert.equal(questionTitle({title:'컬럼 passed는 무엇이며 bin가 무엇을 뜻하나요?',targets}),'passed 컬럼은 무엇이며 bin 컬럼이 무엇을 뜻하나요?');
 assert.equal(questionTitle({title:'passed를 bin와 비교하나요?',targets}),'passed 컬럼을 bin 컬럼과 비교하나요?');
});

test('inline badges preserve sentence order and never badge quoted values',()=>{
 const question={title:"passed는 'passed 컬럼' 값과 다른가요?",targets:[column('passed')]};
 const parts=questionTitleParts(question);
 assert.deepEqual(parts.filter(p=>p.column).map(p=>p.text),['passed 컬럼']);
 assert.equal(parts.map(p=>p.text).join(''),questionTitle(question));
 assert.equal(question.title,"passed는 'passed 컬럼' 값과 다른가요?");
});
