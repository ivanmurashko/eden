/*
 * Copyright (c) Facebook, Inc. and its affiliates.
 *
 * This software may be used and distributed according to the terms of the
 * GNU General Public License version 2.
 */

use std::collections::BTreeSet;

use anyhow::Error;
use futures::future::FutureExt;
use futures::stream::{self, TryStreamExt};
use maplit::hashmap;
use pretty_assertions::assert_eq;
use tokio::task::yield_now;

use super::utils::{StateLog, Tick};
use crate::{
    bounded_traversal, bounded_traversal_dag, bounded_traversal_stream, bounded_traversal_stream2,
};

// Tree for test purposes
struct Tree {
    id: usize,
    children: Vec<Tree>,
}

impl Tree {
    fn new(id: usize, children: Vec<Tree>) -> Self {
        Self { id, children }
    }

    fn leaf(id: usize) -> Self {
        Self::new(id, vec![])
    }
}

#[tokio::test]
async fn test_bounded_traversal() -> Result<(), Error> {
    // tree
    //      0
    //     / \
    //    1   2
    //   /   / \
    //  5   3   4
    let tree = Tree::new(
        0,
        vec![
            Tree::new(1, vec![Tree::leaf(5)]),
            Tree::new(2, vec![Tree::leaf(3), Tree::leaf(4)]),
        ],
    );

    let tick = Tick::new();
    let log: StateLog<String> = StateLog::new();
    let reference: StateLog<String> = StateLog::new();

    let traverse = bounded_traversal(
        2, // level of parallelism
        tree,
        // unfold
        {
            let tick = tick.clone();
            let log = log.clone();
            move |Tree { id, children }| {
                let log = log.clone();
                tick.sleep(1).map(move |now| {
                    log.unfold(id, now);
                    Ok::<_, Error>((id, children))
                })
            }
        },
        // fold
        {
            let tick = tick.clone();
            let log = log.clone();
            move |id, children| {
                let log = log.clone();
                tick.sleep(1).map(move |now| {
                    let value = id.to_string() + &children.collect::<String>();
                    log.fold(id, now, value.clone());
                    Ok::<_, Error>(value)
                })
            }
        },
    )
    .boxed();
    let handle = tokio::spawn(traverse);

    yield_now().await;
    assert_eq!(log, reference);

    tick.tick().await;
    reference.unfold(0, 1);
    assert_eq!(log, reference);

    tick.tick().await;
    reference.unfold(1, 2);
    reference.unfold(2, 2);
    assert_eq!(log, reference);

    // only two unfolds executet because of the parallelism constraint
    tick.tick().await;
    reference.unfold(5, 3);
    reference.unfold(4, 3);
    assert_eq!(log, reference);

    tick.tick().await;
    reference.fold(4, 4, "4".to_string());
    reference.fold(5, 4, "5".to_string());
    assert_eq!(log, reference);

    tick.tick().await;
    reference.fold(1, 5, "15".to_string());
    reference.unfold(3, 5);
    assert_eq!(log, reference);

    tick.tick().await;
    reference.fold(3, 6, "3".to_string());
    assert_eq!(log, reference);

    tick.tick().await;
    reference.fold(2, 7, "234".to_string());
    assert_eq!(log, reference);

    tick.tick().await;
    reference.fold(0, 8, "015234".to_string());
    assert_eq!(log, reference);

    assert_eq!(handle.await??, "015234");
    Ok(())
}

#[tokio::test]
async fn test_bounded_traversal_dag() -> Result<(), Error> {
    // dag
    //   0
    //  / \
    // 1   2
    //  \ / \
    //   3   4
    //  / \
    // 5   6
    //  \ /
    //   7
    //   |
    //   4 - will be resolved by the time it is reached
    let dag = hashmap! {
        0 => vec![1, 2],
        1 => vec![3],
        2 => vec![3, 4],
        3 => vec![5, 6],
        4 => vec![],
        5 => vec![7],
        6 => vec![7],
        7 => vec![4],
    };

    let tick = Tick::new();
    let log: StateLog<String> = StateLog::new();
    let reference: StateLog<String> = StateLog::new();

    let traverse = bounded_traversal_dag(
        2, // level of parallelism
        0,
        // unfold
        {
            let tick = tick.clone();
            let log = log.clone();
            move |id| {
                let log = log.clone();
                let children = dag.get(&id).cloned().unwrap_or_default();
                tick.sleep(1).map(move |now| {
                    log.unfold(id, now);
                    Ok::<_, Error>((id, children))
                })
            }
        },
        // fold
        {
            let tick = tick.clone();
            let log = log.clone();
            move |id, children| {
                let log = log.clone();
                tick.sleep(1).map(move |now| {
                    let value = id.to_string() + &children.collect::<String>();
                    log.fold(id, now, value.clone());
                    Ok(value)
                })
            }
        },
    )
    .boxed();
    let handle = tokio::spawn(traverse);

    yield_now().await;
    assert_eq!(log, reference);

    tick.tick().await;
    reference.unfold(0, 1);
    assert_eq!(log, reference);

    tick.tick().await;
    reference.unfold(1, 2);
    reference.unfold(2, 2);
    assert_eq!(log, reference);

    tick.tick().await;
    reference.unfold(3, 3);
    reference.unfold(4, 3);
    assert_eq!(log, reference);

    tick.tick().await;
    reference.fold(4, 4, "4".to_string());
    reference.unfold(6, 4);
    assert_eq!(log, reference);

    tick.tick().await;
    reference.unfold(5, 5);
    reference.unfold(7, 5);
    assert_eq!(log, reference);

    tick.tick().await;
    reference.fold(7, 6, "74".to_string());
    assert_eq!(log, reference);

    tick.tick().await;
    reference.fold(5, 7, "574".to_string());
    reference.fold(6, 7, "674".to_string());
    assert_eq!(log, reference);

    tick.tick().await;
    reference.fold(3, 8, "3574674".to_string());
    assert_eq!(log, reference);

    tick.tick().await;
    reference.fold(1, 9, "13574674".to_string());
    reference.fold(2, 9, "235746744".to_string());
    assert_eq!(log, reference);

    tick.tick().await;
    reference.fold(0, 10, "013574674235746744".to_string());
    assert_eq!(log, reference);

    assert_eq!(handle.await??, Some("013574674235746744".to_string()));
    Ok(())
}

#[tokio::test]
async fn test_bounded_traversal_dag_with_cycle() -> Result<(), Error> {
    // graph with cycle
    //   0
    //  / \
    // 1   2
    //  \ /
    //   3
    //   |
    //   2 <- forms cycle
    let graph = hashmap! {
        0 => vec![1, 2],
        1 => vec![3],
        2 => vec![3],
        3 => vec![2],
    };

    let tick = Tick::new();
    let log: StateLog<String> = StateLog::new();
    let reference: StateLog<String> = StateLog::new();

    let traverse = bounded_traversal_dag(
        2, // level of parallelism
        0,
        // unfold
        {
            let tick = tick.clone();
            let log = log.clone();
            move |id| {
                let log = log.clone();
                let children = graph.get(&id).cloned().unwrap_or_default();
                tick.sleep(1).map(move |now| {
                    log.unfold(id, now);
                    Ok::<_, Error>((id, children))
                })
            }
        },
        // fold
        {
            let tick = tick.clone();
            let log = log.clone();
            move |id, children| {
                let log = log.clone();
                tick.sleep(1).map(move |now| {
                    let value = id.to_string() + &children.collect::<String>();
                    log.fold(id, now, value.clone());
                    Ok(value)
                })
            }
        },
    )
    .boxed();
    let handle = tokio::spawn(traverse);

    yield_now().await;
    assert_eq!(log, reference);

    tick.tick().await;
    reference.unfold(0, 1);
    assert_eq!(log, reference);

    tick.tick().await;
    reference.unfold(1, 2);
    reference.unfold(2, 2);
    assert_eq!(log, reference);

    tick.tick().await;
    reference.unfold(3, 3);
    assert_eq!(log, reference);

    assert_eq!(handle.await??, None); // cycle detected
    Ok(())
}

#[tokio::test]
async fn test_bounded_traversal_stream() -> Result<(), Error> {
    // tree
    //      0
    //     / \
    //    1   2
    //   /   / \
    //  5   3   4
    let tree = Tree::new(
        0,
        vec![
            Tree::new(1, vec![Tree::leaf(5)]),
            Tree::new(2, vec![Tree::leaf(3), Tree::leaf(4)]),
        ],
    );

    let tick = Tick::new();
    let log: StateLog<BTreeSet<usize>> = StateLog::new();
    let reference: StateLog<BTreeSet<usize>> = StateLog::new();

    let traverse = bounded_traversal_stream(2, Some(tree), {
        let tick = tick.clone();
        let log = log.clone();
        move |Tree { id, children }| {
            let log = log.clone();
            tick.sleep(1).map(move |now| {
                log.unfold(id, now);
                Ok::<_, Error>((id, children))
            })
        }
    })
    .try_collect::<BTreeSet<usize>>()
    .boxed();
    let handle = tokio::spawn(traverse);

    yield_now().await;
    assert_eq!(log, reference);

    tick.tick().await;
    reference.unfold(0, 1);
    assert_eq!(log, reference);

    tick.tick().await;
    reference.unfold(1, 2);
    reference.unfold(2, 2);
    assert_eq!(log, reference);

    tick.tick().await;
    reference.unfold(5, 3);
    reference.unfold(4, 3);
    assert_eq!(log, reference);

    tick.tick().await;
    reference.unfold(3, 4);
    assert_eq!(log, reference);

    assert_eq!(handle.await??, (0..6).collect::<BTreeSet<_>>());
    Ok(())
}

#[tokio::test]
async fn test_bounded_traversal_stream2() -> Result<(), Error> {
    // tree
    //      0
    //     / \
    //    1   2
    //   /   / \
    //  5   3   4
    let tree = Tree::new(
        0,
        vec![
            Tree::new(1, vec![Tree::leaf(5)]),
            Tree::new(2, vec![Tree::leaf(3), Tree::leaf(4)]),
        ],
    );

    let tick = Tick::new();
    let log: StateLog<BTreeSet<usize>> = StateLog::new();
    let reference: StateLog<BTreeSet<usize>> = StateLog::new();

    let traverse = bounded_traversal_stream2(2, Some(tree), {
        let tick = tick.clone();
        let log = log.clone();
        move |Tree { id, children }| {
            let log = log.clone();
            tick.sleep(1).map(move |now| {
                log.unfold(id, now);
                Ok::<_, Error>((id, stream::iter(children.into_iter().map(Ok))))
            })
        }
    })
    .try_collect::<BTreeSet<usize>>()
    .boxed();
    let handle = tokio::spawn(traverse);

    yield_now().await;
    assert_eq!(log, reference);

    tick.tick().await;
    reference.unfold(0, 1);
    assert_eq!(log, reference);

    tick.tick().await;
    reference.unfold(1, 2);
    reference.unfold(2, 2);
    assert_eq!(log, reference);

    tick.tick().await;
    reference.unfold(5, 3);
    reference.unfold(3, 3);
    assert_eq!(log, reference);

    tick.tick().await;
    reference.unfold(4, 4);
    assert_eq!(log, reference);

    assert_eq!(handle.await??, (0..6).collect::<BTreeSet<_>>());
    Ok(())
}
