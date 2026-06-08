<?php
// +----------------------------------------------------------------------
// | ThinkPHP [ WE CAN DO IT JUST THINK ]
// +----------------------------------------------------------------------
// | Copyright (c) 2006-2016 http://thinkphp.cn All rights reserved.
// +----------------------------------------------------------------------
// | Licensed ( http://www.apache.org/licenses/LICENSE-2.0 )
// +----------------------------------------------------------------------
// | Author: 流年 <liu21st@gmail.com>
// +----------------------------------------------------------------------

// 应用公共文件
use Symfony\Component\Yaml\Exception\DumpException;
use think\Controller;
use think\Db;

//定义获取分页表函数
function split_query($sql, $allsql)
{
  $result_array = Db::query($sql);
  $allresult = Db::query('SELECT count(*) as num FROM (' . $allsql . ') as A');
  $ret = [
    'total' => $allresult[0]['num'],
    'rows' => $result_array,
  ];
  return ($ret);
}
//sql语句导出函数
function query($allsql, $headlist)
{
  //让程序一直运行
  set_time_limit(0);
  //设置程序运行内存
  ini_set('memory_limit', '128M');
  $fileName = 'download';
  header('Content-Encoding: UTF-8');
  header("Content-type:application/vnd.ms-excel;charset=UTF-8");
  header('Content-Disposition: attachment;filename="' . $fileName . '.csv"');
  //打开php标准输出流
  $fp = fopen('php://output', 'a');
  //添加BOM头，以UTF8编码导出CSV文件，如果文件头未添加BOM头，打开会出现乱码。
  fwrite($fp, chr(0xEF) . chr(0xBB) . chr(0xBF));
  fputcsv($fp, $headlist);
  $allresult = Db::query('SELECT count(*) as num FROM (' . $allsql . ') as A');
  $step = ceil($allresult[0]['num'] / 10000); //循环
  $nums = 10000; //每次导出数量
  for ($i = 0; $i < $step; $i++) {
    $start = $i * 10000;
    $sql = $allsql . " LIMIT {$start},{$nums}";
    $result = Db::query($sql);
    foreach ($result as $item) {
      fputcsv($fp, $item);
    }
    //每5000条数据就刷新缓冲区
    ob_flush();
    flush();
  }
}
//fasta导出
//sql语句导出函数
function fasta($allsql, $type)
{
  $seqsql = 'SELECT seq.id,seq.' . $type . ' FROM (' . $allsql . ') as A join seq on seq.id=A.id';
  //让程序一直运行
  set_time_limit(0);
  //设置程序运行内存
  ini_set('memory_limit', '256M');
  $fileName = $type;
  header('Content-Encoding: UTF-8');
  header("Content-Type: application/octet-stream");
  header('Content-Disposition: attachment;filename="' . $fileName . '.fa"');
  //打开php标准输出流
  $fp = fopen('php://output', 'a');
  $allresult = Db::query($allsql);
  $step = ceil(count($allresult) / 10000); //循环次数
  $nums = 10000; //每次导出数量
  for ($i = 0; $i < $step; $i++) {
    $start = $i * 10000;
    $sql = $seqsql . " LIMIT {$start},{$nums}";
    $result = Db::query($sql);
    foreach ($result as $item) {
      $content = '>' . $item['id'] . "\n" . $item[$type] . "\n";
      fwrite($fp, $content);
    }
    //每2万条数据就刷新缓冲区
    ob_flush();
    flush();
  }
  fclose($fp);
}
function makestr($array)
{
  $merge_array = array();
  for ($i = 0; $i < count($array); $i++) {
    array_push($merge_array, "'" . $array[$i] . "'");
  }
  $out = implode(",", $merge_array);
  return $out;
}

function changeids_func($v)
{
  return "(" . $v . ")";
}

function myQuotemeta($str)
{
  $str = preg_replace('/\s+/', '', $str);
  $str = str_replace('\\', '\\\\', $str);
  $str = str_replace('*', '\\\*', $str);
  $str = str_replace('|', '\\\|', $str);
  $str = str_replace('^', '\\\^', $str);
  $str = str_replace('$', '\\\$', $str);
  $str = str_replace('.', '\\\.', $str);
  $str = str_replace('+', '\\\+', $str);
  $str = str_replace('[', '\\\[', $str);
  $str = str_replace(']', '\\\]', $str);
  $str = str_replace('{', '\\\{', $str);
  $str = str_replace('}', '\\\}', $str);
  $str = str_replace('(', '\\\(', $str);
  $str = str_replace(')', '\\\)', $str);

  return $str;
}
